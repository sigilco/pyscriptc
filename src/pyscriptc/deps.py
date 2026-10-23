"""`pyscriptc deps`: resolve PyPI dependencies and materialise pure-Python wheels.

Partial slice of PyPI support (design: `.agents/plans/2026-10-21-pypi-support-design.md`).
Dependencies come from a PEP 723 `# /// script` block or `[project].dependencies`
in `pyproject.toml`. `uv` resolves them (universal, for the host CPython
minor) into a PEP 751 lock (`pylock.*.toml`), never building an sdist, so no
`setup.py` or build backend runs. Packages behind environment markers stay in
the lock and are classified too, so the lane table is a superset. Every locked
distribution lands in one lane:

- `pure`: a `none-any` wheel exists. It is downloaded, checked against the
  lock's sha256 and its own RECORD, and unpacked into the content-addressed
  cache `~/.cache/pyscriptc/deps/src/<sha256>/` for compile-from-source.
- `native`: only interpreter or platform specific wheels exist, or the wheel
  ships extension modules. Needs a replacement pack, the `extern` C lane or
  the opt-in island.
- `sdist`: no wheel at all. Building it would run arbitrary code, so it is
  refused by default.

Nothing here touches lowering; the compiler side reads the report's
`sourceRoots` later.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from pyscriptc.config import ConfigError, read_pep723
from pyscriptc.errors import SpikeError
from pyscriptc.log import get_logger

DEPS_CODE = "PS3101"
LOCK_TOOL_KEY = "pyscriptc"
EXTENSION_SUFFIXES = (".so", ".pyd", ".dylib", ".dll")
SKIPPED_DATA_DIRS = ("scripts", "headers", "data")
SKIPPED_DIR_NAMES = ("tests", "test", "docs", "examples")

log = get_logger()

NAME_RE = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")

LANE_HINTS = {
    "pure": "compile from source",
    "native": "needs a replacement pack, an `extern` C library lane or the opt-in island; refused by default",
    "sdist": "no wheel published; building an sdist runs arbitrary code, refused by default",
}


class DepsError(SpikeError):
    def __init__(self, message: str) -> None:
        super().__init__(DEPS_CODE, message)


def _toml_loads(text: str) -> dict[str, Any]:
    try:
        import tomllib
    except ModuleNotFoundError as exc:  # Python 3.10
        raise DepsError("dependency metadata needs Python 3.11 or newer (tomllib)") from exc
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise DepsError(f"invalid TOML: {exc}") from exc


# ---------------------------------------------------------------- spec


@dataclass(frozen=True)
class DepSpec:
    """What the user asked for: one script or one project."""

    origin: Path
    kind: str  # "script" or "project"
    requires_python: str | None
    dependencies: tuple[str, ...]

    @property
    def root(self) -> Path:
        return self.origin.parent

    @property
    def lock_path(self) -> Path:
        if self.kind == "script":
            return self.root / f"pylock.{self.origin.stem}.toml"
        return self.root / "pylock.pyscriptc.toml"

    @property
    def uv_lock(self) -> Path:
        """uv's own lock, honoured when present (never written by us)."""
        if self.kind == "script":
            return self.origin.with_name(self.origin.name + ".lock")
        return self.root / "uv.lock"


def normalize_name(name: str) -> str:
    """PEP 503 normalised project name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str:
    match = NAME_RE.match(requirement)
    if match is None:
        raise DepsError(f"cannot read a project name from requirement {requirement!r}")
    return normalize_name(match.group(1))


def _check_requirements(value: Any, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise DepsError(f"{where} must be a list of requirement strings")
    for item in value:
        requirement_name(item)
    return tuple(value)


def load_spec(path: Path) -> DepSpec:
    """Read dependencies from a `.py` script, a `pyproject.toml` or a directory holding one."""
    path = path.resolve()
    if path.is_dir():
        path = path / "pyproject.toml"
    if not path.is_file():
        raise DepsError(f"{path}: not found (expected a .py script, a pyproject.toml or a project directory)")
    text = path.read_text(encoding="utf-8")
    if path.name == "pyproject.toml":
        project = _toml_loads(text).get("project", {})
        deps = _check_requirements(project.get("dependencies"), "[project].dependencies")
        return DepSpec(path, "project", project.get("requires-python"), deps)
    try:
        meta = read_pep723(text) or {}
    except ConfigError as exc:
        raise DepsError(str(exc)) from exc
    deps = _check_requirements(meta.get("dependencies"), "PEP 723 `dependencies`")
    return DepSpec(path, "script", meta.get("requires-python"), deps)


# ---------------------------------------------------------------- lock


@dataclass(frozen=True)
class LockedWheel:
    url: str
    sha256: str
    filename: str

    @property
    def tags(self) -> tuple[str, str, str]:
        parts = self.filename[: -len(".whl")].split("-")
        if len(parts) < 5:
            raise DepsError(f"malformed wheel filename {self.filename!r}")
        return parts[-3], parts[-2], parts[-1]


@dataclass(frozen=True)
class LockedPackage:
    name: str
    version: str
    wheels: tuple[LockedWheel, ...]
    has_sdist: bool
    marker: str | None = None


def host_python() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def input_digest(spec: DepSpec, python: str) -> str:
    """Stable digest of everything that feeds resolution; a lock with another digest is stale."""
    payload = json.dumps(
        {"deps": sorted(spec.dependencies), "requires-python": spec.requires_python, "python": python},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def parse_pylock(text: str) -> list[LockedPackage]:
    data = _toml_loads(text)
    if not str(data.get("lock-version", "")).startswith("1."):
        raise DepsError(f"unsupported pylock lock-version {data.get('lock-version')!r}")
    packages: list[LockedPackage] = []
    for entry in data.get("packages", []):
        wheels = []
        for wheel in entry.get("wheels", []):
            url = wheel.get("url") or wheel.get("path") or ""
            sha = wheel.get("hashes", {}).get("sha256")
            if not sha:
                raise DepsError(f"{entry.get('name')}: locked wheel without a sha256 hash ({url})")
            filename = wheel.get("name") or url.rsplit("/", 1)[-1]
            wheels.append(LockedWheel(url, sha.lower(), filename))
        packages.append(
            LockedPackage(
                normalize_name(entry["name"]),
                str(entry.get("version", "")),
                tuple(wheels),
                "sdist" in entry,
                entry.get("marker"),
            )
        )
    return sorted(packages, key=lambda p: p.name)


def lock_digest(text: str) -> str | None:
    tool = _toml_loads(text).get("tool", {}).get(LOCK_TOOL_KEY, {})
    value = tool.get("input-digest")
    return value if isinstance(value, str) else None


def uv_binary() -> str:
    found = os.environ.get("PYSC_UV") or shutil.which("uv")
    if not found:
        raise DepsError("`uv` not found on PATH (set PYSC_UV); install it from https://docs.astral.sh/uv/")
    return found


def _run_uv(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Seam for tests: every uv call goes through here."""
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)


def _uv(args: list[str], cwd: Path) -> str:
    proc = _run_uv([uv_binary(), *args], cwd)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        hint = ""
        if "building from source is disabled" in detail or "--no-build" in detail:
            hint = "\nhint: a dependency publishes no wheel; pyscriptc never builds sdists (no setup.py execution)"
        raise DepsError(f"uv {' '.join(args[:2])} failed:\n{detail}{hint}")
    return proc.stdout


def resolve(spec: DepSpec, *, frozen: bool = False, upgrade: bool = False, offline: bool = False) -> tuple[str, str]:
    """Return (pylock text, provenance). Provenance is `uv.lock`, `lock` (reused) or `resolved`."""
    common = ["--offline"] if offline else []
    if spec.uv_lock.is_file():
        args = ["export", "--format", "pylock.toml", "--frozen", "--no-dev", "--no-emit-project", "--no-header"]
        if spec.kind == "script":
            args += ["--script", str(spec.origin)]
        return _uv([*args, *common], spec.root), "uv.lock"

    python = host_python()
    digest = input_digest(spec, python)
    lock = spec.lock_path
    if lock.is_file() and not upgrade:
        text = lock.read_text(encoding="utf-8")
        if lock_digest(text) == digest:
            return text, "lock"
        if frozen:
            raise DepsError(f"{lock.name} is stale for the current dependencies; rerun without --frozen")
    elif frozen:
        raise DepsError(f"--frozen needs {lock.name} (or uv's lock) beside {spec.origin.name}")
    if not spec.dependencies:
        text = 'lock-version = "1.0"\ncreated-by = "pyscriptc"\n'
    else:
        with tempfile.TemporaryDirectory(prefix="pyscriptc-deps-") as tmp:
            req = Path(tmp) / "requirements.in"
            out = Path(tmp) / "pylock.toml"
            req.write_text("\n".join(spec.dependencies) + "\n", encoding="utf-8")
            args = ["pip", "compile", str(req), "--format", "pylock.toml", "-o", str(out)]
            args += ["--no-build", "--universal", "--python-version", python, "--no-header", "--quiet", *common]
            if upgrade:
                args.append("--upgrade")
            _uv(args, spec.root)
            text = out.read_text(encoding="utf-8")
    text = text.rstrip("\n") + f'\n\n[tool.{LOCK_TOOL_KEY}]\ninput-digest = "{digest}"\npython = "{python}"\n'
    lock.write_text(text, encoding="utf-8")
    return text, "resolved"


# ---------------------------------------------------------------- classify


def _python_tag_ok(tag: str, python: str) -> bool:
    major, minor = (int(x) for x in python.split("."))
    for one in tag.split("."):
        if one in ("py3", f"py{major}"):
            return True
        m = re.fullmatch(r"(?:py|cp)(\d)(\d+)", one)
        if m and int(m.group(1)) == major and int(m.group(2)) <= minor:
            return True
    return False


def is_pure_wheel(wheel: LockedWheel, python: str | None = None) -> bool:
    """`none-any` wheel usable by `python` (default: the host)."""
    py, abi, plat = wheel.tags
    return abi == "none" and plat == "any" and _python_tag_ok(py, python or host_python())


def select_pure_wheel(pkg: LockedPackage, python: str | None = None) -> LockedWheel | None:
    pure = [w for w in pkg.wheels if is_pure_wheel(w, python)]
    return sorted(pure, key=lambda w: w.filename)[0] if pure else None


def classify_tags(pkg: LockedPackage, python: str | None = None) -> tuple[str, str]:
    """Lane and reason from wheel tags alone (no download)."""
    if select_pure_wheel(pkg, python) is not None:
        return "pure", "none-any wheel"
    if pkg.wheels:
        abis = ", ".join(sorted({w.tags[1] for w in pkg.wheels}))
        return "native", f"only ABI or platform specific wheels ({len(pkg.wheels)} wheels, ABI {abis})"
    if pkg.has_sdist:
        return "sdist", "source distribution only"
    return "sdist", "no wheel in the lock"


def _record_hash(digest: str) -> str:
    algo, _, value = digest.partition("=")
    if algo != "sha256":
        raise DepsError(f"RECORD uses unsupported hash {algo!r}")
    return value


def inspect_wheel(data: bytes) -> tuple[bool, str]:
    """(pure, reason) from wheel contents. Raises DepsError on a tampered or unsafe archive."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        for info in zf.infolist():
            _safe_member(info)
        dist_infos = {n.split("/", 1)[0] for n in names if n.split("/", 1)[0].endswith(".dist-info")}
        if len(dist_infos) != 1:
            raise DepsError("wheel must contain exactly one .dist-info directory")
        dist_info = dist_infos.pop()
        record_name = f"{dist_info}/RECORD"
        if record_name not in names:
            raise DepsError("wheel has no RECORD")
        recorded: dict[str, str] = {}
        for row in csv.reader(io.StringIO(zf.read(record_name).decode("utf-8"))):
            if row and row[0] and len(row) > 1 and row[1]:
                recorded[row[0]] = _record_hash(row[1])
        for name in names:
            if name.endswith("/") or name == record_name or name.endswith((".jws", ".p7s")):
                continue
            want = recorded.get(name)
            if want is None:
                raise DepsError(f"{name} is not listed in RECORD")
            got = base64.urlsafe_b64encode(hashlib.sha256(zf.read(name)).digest()).rstrip(b"=").decode()
            if got != want:
                raise DepsError(f"{name} does not match its RECORD hash")
        wheel_meta = zf.read(f"{dist_info}/WHEEL").decode("utf-8") if f"{dist_info}/WHEEL" in names else ""
        native = [n for n in names if n.lower().endswith(EXTENSION_SUFFIXES)]
        if native:
            return False, f"ships extension modules ({native[0]}{', ...' if len(native) > 1 else ''})"
        if re.search(r"(?mi)^Root-Is-Purelib:\s*false\s*$", wheel_meta):
            return False, "WHEEL says Root-Is-Purelib: false"
    return True, "none-any wheel, no extension modules"


def _safe_member(info: zipfile.ZipInfo) -> None:
    path = PurePosixPath(info.filename)
    if path.is_absolute() or ".." in path.parts or "\\" in info.filename or ":" in path.parts[0]:
        raise DepsError(f"unsafe path in wheel: {info.filename!r}")
    if stat.S_ISLNK(info.external_attr >> 16):
        raise DepsError(f"symlink in wheel: {info.filename!r}")


# ---------------------------------------------------------------- cache


def cache_root(override: str | Path | None = None) -> Path:
    base = override or os.environ.get("PYSC_CACHE_DIR")
    return (Path(base) if base else Path.home() / ".cache" / "pyscriptc") / "deps"


def _urlopen(url: str) -> bytes:
    """Seam for tests: the only network read outside uv."""
    if not url.startswith("https://"):
        raise DepsError(f"refusing non-https wheel URL {url!r}")
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 (scheme checked above)
        return resp.read()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)


def fetch_wheel(wheel: LockedWheel, cache: Path, *, offline: bool = False) -> bytes:
    """Wheel bytes from the blob cache or the network, verified against the lock's sha256."""
    blob = cache / "blobs" / "sha256" / wheel.sha256[:2] / f"{wheel.sha256}.whl"
    if blob.is_file():
        data = blob.read_bytes()
        if hashlib.sha256(data).hexdigest() == wheel.sha256:
            return data
        blob.unlink()
    if offline:
        raise DepsError(f"{wheel.filename} is not cached and --offline is set")
    data = _urlopen(wheel.url)
    got = hashlib.sha256(data).hexdigest()
    if got != wheel.sha256:
        raise DepsError(f"{wheel.filename}: sha256 {got} does not match the lock ({wheel.sha256})")
    _atomic_write(blob, data)
    return data


def _should_skip_member(parts: tuple[str, ...]) -> bool:
    if not parts:
        return True
    if any(part.endswith(".dist-info") for part in parts):
        return True
    if parts[0].endswith(".data"):
        if len(parts) < 3 or parts[1] in SKIPPED_DATA_DIRS:
            return True
        parts = parts[2:]
        if not parts:
            return True
    if any(part in SKIPPED_DIR_NAMES for part in parts[:-1]):
        return True
    filename = parts[-1]
    if (filename.startswith("test_") and filename.endswith(".py")) or filename == "conftest.py":
        return True
    return False


def materialise(data: bytes, sha256: str, cache: Path, *, name: str | None = None) -> Path:
    """Unpack a verified pure wheel into `src/<sha256>/` once; returns the import root."""
    dest = cache / "src" / sha256
    if (dest / ".complete").is_file():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=dest.parent, prefix=".tmp-"))
    skipped: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                _safe_member(info)
                if info.is_dir():
                    continue
                parts = PurePosixPath(info.filename).parts
                if _should_skip_member(parts):
                    skipped.append(info.filename)
                    continue
                if parts[0].endswith(".data"):
                    parts = parts[2:]  # purelib/platlib content lands at the root
                target = tmp.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(info))
        (tmp / ".complete").write_text(sha256 + "\n", encoding="utf-8")
        if dest.exists():
            shutil.rmtree(dest)
        os.replace(tmp, dest)
        log.info(
            "deps.materialise",
            name=name or "",
            sha256=sha256,
            source_root=str(dest),
            skipped={"count": len(skipped), "paths": skipped},
        )
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return dest


# ---------------------------------------------------------------- report


@dataclass
class DepResult:
    name: str
    version: str
    lane: str
    reason: str
    wheel: str | None = None
    sha256: str | None = None
    source_root: str | None = None
    marker: str | None = None
    hint: str = field(init=False)

    def __post_init__(self) -> None:
        self.hint = LANE_HINTS[self.lane]


def analyse(
    packages: list[LockedPackage], cache: Path, *, download: bool = True, offline: bool = False
) -> list[DepResult]:
    results: list[DepResult] = []
    for pkg in packages:
        lane, reason = classify_tags(pkg)
        result = DepResult(pkg.name, pkg.version, lane, reason, marker=pkg.marker)
        wheel = select_pure_wheel(pkg)
        if wheel is not None:
            result.wheel, result.sha256 = wheel.filename, wheel.sha256
            if download:
                data = fetch_wheel(wheel, cache, offline=offline)
                pure, reason = inspect_wheel(data)
                result.reason = reason
                if pure:
                    result.source_root = str(materialise(data, wheel.sha256, cache, name=pkg.name))
                else:
                    result.lane = "native"
                    result.hint = LANE_HINTS["native"]
        results.append(result)
    return results


def run_deps(
    source: Path,
    *,
    frozen: bool = False,
    upgrade: bool = False,
    download: bool = True,
    offline: bool = False,
    cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    spec = load_spec(source)
    text, provenance = resolve(spec, frozen=frozen, upgrade=upgrade, offline=offline)
    cache = cache_root(cache_dir)
    results = analyse(parse_pylock(text), cache, download=download, offline=offline)
    lanes: dict[str, int] = {}
    for r in results:
        lanes[r.lane] = lanes.get(r.lane, 0) + 1
    return {
        "source": str(spec.origin),
        "kind": spec.kind,
        "python": host_python(),
        "requiresPython": spec.requires_python,
        "dependencies": list(spec.dependencies),
        "lock": str(spec.uv_lock if provenance == "uv.lock" else spec.lock_path),
        "lockProvenance": provenance,
        "cache": str(cache),
        "lanes": dict(sorted(lanes.items())),
        "packages": [vars(r) for r in results],
        "sourceRoots": [r.source_root for r in results if r.source_root],
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [f"{report['source']}: {len(report['packages'])} locked distribution(s), lock {report['lockProvenance']}"]
    if not report["packages"]:
        lines.append("  no dependencies")
    width = max((len(p["name"]) + len(p["version"]) for p in report["packages"]), default=0) + 1
    for p in report["packages"]:
        label = f"{p['name']} {p['version']}".ljust(width)
        lines.append(f"  {label}  {p['lane']:<6}  {p['reason']}")
        if p["lane"] != "pure":
            lines.append(f"  {'':<{width}}  {'':<6}  hint: {p['hint']}")
    if report["lanes"]:
        lines.append("lanes: " + ", ".join(f"{k} {v}" for k, v in report["lanes"].items()))
    return "\n".join(lines)


def register(sub: Any, common: argparse.ArgumentParser) -> None:
    """Add the `deps` subcommand to the CLI's subparsers."""
    parser = sub.add_parser(
        "deps",
        help="resolve PyPI dependencies (PEP 723 or pyproject) and cache pure-Python wheels",
        parents=[common],
    )
    parser.add_argument("source", help="a .py script with a `# /// script` block, a pyproject.toml or its directory")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--frozen", action="store_true", help="use the existing lock, never resolve")
    parser.add_argument("--upgrade", action="store_true", help="re-resolve to the newest allowed versions")
    parser.add_argument("--offline", action="store_true", help="no network: uv offline, cached wheels only")
    parser.add_argument("--no-download", action="store_true", help="classify from wheel tags only")
    parser.add_argument(
        "--cache-dir", metavar="DIR", help="cache root (env PYSC_CACHE_DIR, default ~/.cache/pyscriptc)"
    )


def deps_cmd(args: argparse.Namespace) -> int:
    """Exit 0 when every dependency is on the pure lane, 3 otherwise (like `--print diagnostics`)."""
    report = run_deps(
        Path(args.source),
        frozen=args.frozen,
        upgrade=args.upgrade,
        download=not args.no_download,
        offline=args.offline,
        cache_dir=args.cache_dir,
    )
    print(json.dumps(report, indent=2) if args.json else render_text(report))
    return 0 if all(p["lane"] == "pure" for p in report["packages"]) else 3
