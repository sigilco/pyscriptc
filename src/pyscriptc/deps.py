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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict

from pyscriptc.config import ConfigError, read_pep723
from pyscriptc.errors import SpikeError
from pyscriptc.log import get_logger

DEPS_CODE = "PS3101"
LOCK_TOOL_KEY = "pyscriptc"
EXTENSION_SUFFIXES = (".so", ".pyd", ".dylib", ".dll")
SKIPPED_ANYWHERE = "tests"
UNKNOWN_IMPORT_NAMES = "unknown"
UNPACK_SCHEMA_VERSION = 2
SDIST = 1

log = get_logger()

NAME_RE = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")

LANE_HINTS = {
    "pure": "compile from source",
    "native": "needs a replacement pack, an `extern` C library lane or the opt-in island; refused by default",
    "sdist": "no wheel published; building an sdist runs arbitrary code, refused by default",
    "island": "island lane: requires libpython; the binary links the host interpreter",
}

# The `[imports]` policy lane hints, used when the table overrides the lane a
# locked dependency would otherwise take. The island is only ever selected
# here, so its hint is the label the binary carries.
POLICY_HINTS = {
    "replace": "replaced by a curated pack",
    "extern": "bound through a C ABI extern manifest",
    "island": LANE_HINTS["island"],
    "refuse": "refused by the `[imports]` policy",
}


class DepsError(SpikeError):
    def __init__(self, message: str) -> None:
        super().__init__(DEPS_CODE, message)


def _toml_loads(text: str) -> Dict[str, Any]:
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
    first_requirement: str
    all_requirements: str  # newline-joined, "" when none

    def requirement_list(self) -> list[str]:
        """The requirement strings in order, [] when none."""
        if not self.all_requirements:
            return []
        return self.all_requirements.split("\n")

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


def _pep723_table(meta: Dict[str, Any] | None) -> Dict[str, Any]:
    """The PEP 723 table, `{}` when the script block is absent."""
    if meta is None:
        return {}
    return meta


def _opt_str(table: Dict[str, Any], key: str) -> str | None:
    """A string-valued table entry, None when absent (Any read proven at the site)."""
    if key not in table:
        return None
    value: str = table[key]
    return value


def _opt_list(table: Dict[str, Any], key: str) -> list[str] | None:
    """A list-of-strings table entry, None when absent or mistyped."""
    if key not in table:
        return None
    value: list[str] = table[key]
    if not isinstance(value, list):
        raise DepsError(f"{key} must be a list of requirement strings")
    for item in value:
        if not isinstance(item, str):
            raise DepsError(f"{key} must be a list of requirement strings")
    return value


def _check_requirements(value: list[str] | None, where: str) -> tuple[str, str]:
    """Validate the requirement list.

    The tuple carries (first requirement, all requirements joined with
    newlines) so a one-element requirement list and a many-element one
    share the fixed two-field shape the record holds; the empty list is
    ("", "").
    """
    if value is None:
        return ("", "")
    for item in value:
        if not isinstance(item, str):
            raise DepsError(f"{where} must be a list of requirement strings")
        requirement_name(item)
    if not value:
        return ("", "")
    first: str = value[0]
    return (first, "\n".join(value))


def load_spec(path: Path) -> DepSpec:
    """Read dependencies from a `.py` script, a `pyproject.toml` or a directory holding one."""
    path = path.resolve()
    if path.is_dir():
        path = path / "pyproject.toml"
    if not path.is_file():
        raise DepsError(f"{path}: not found (expected a .py script, a pyproject.toml or a project directory)")
    text = path.read_text(encoding="utf-8")
    if path.name == "pyproject.toml":
        data: Dict[str, Any] = _toml_loads(text)
        project: Dict[str, Any] = {}
        if "project" in data:
            project = data["project"]
        checked = _check_requirements(_opt_list(project, "dependencies"), "[project].dependencies")
        wants_python = _opt_str(project, "requires-python")
        return DepSpec(path, "project", wants_python, checked[0], checked[1])
    try:
        meta_block: Dict[str, Any] | None = read_pep723(text)
    except ConfigError as exc:
        failed: Dict[str, Any] | None = None
        meta_block = failed
        raise DepsError(str(exc)) from exc
    meta_table: Dict[str, Any] = _pep723_table(meta_block)
    checked = _check_requirements(_opt_list(meta_table, "dependencies"), "PEP 723 `dependencies`")
    wants_python = _opt_str(meta_table, "requires-python")
    return DepSpec(path, "script", wants_python, checked[0], checked[1])


# ---------------------------------------------------------------- lock


@dataclass(frozen=True)
class LockedWheel:
    url: str
    sha256: str
    filename: str

    def tags(self) -> tuple[str, str, str]:
        """The wheel's (python, abi, platform) tags from its filename."""
        stem: str = self.filename[: -len(".whl")]
        parts: list[str] = stem.split("-")
        if len(parts) < 5:
            raise DepsError(f"malformed wheel filename {self.filename!r}")
        return (parts[-3], parts[-2], parts[-1])


@dataclass(frozen=True)
class LockedPackage:
    name: str
    version: str
    wheels: tuple[LockedWheel, ...]
    flags: int
    marker: str | None = None


def host_python() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def input_digest(spec: DepSpec, python: str) -> str:
    """Stable digest of everything that feeds resolution; a lock with another digest is stale."""
    payload = json.dumps(
        {"deps": spec.requirement_list(), "requires-python": spec.requires_python, "python": python},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def parse_pylock(text: str) -> list[LockedPackage]:
    data: Dict[str, Any] = _toml_loads(text)
    if "lock-version" not in data:
        raise DepsError("unsupported pylock lock-version None")
    version: str = data["lock-version"]
    if not str(version).startswith("1."):
        raise DepsError(f"unsupported pylock lock-version {version!r}")
    packages: list[LockedPackage] = []
    entries: list[Dict[str, Any]] = []
    if "packages" in data:
        entries = data["packages"]
    for entry in entries:
        e: Dict[str, Any] = entry
        wheels: list[LockedWheel] = []
        wheel_rows: list[Dict[str, Any]] = []
        if "wheels" in e:
            wheel_rows = e["wheels"]
        for wheel in wheel_rows:
            w: Dict[str, Any] = wheel
            url: str = ""
            if "url" in w:
                url = w["url"]
            else:
                path: str = ""
                if "path" in w:
                    path = w["path"]
                url = path
            sha: str = ""
            if "hashes" in w:
                hashes: Dict[str, Any] = w["hashes"]
                if "sha256" in hashes:
                    sha = hashes["sha256"]
            if not sha:
                name: str = e["name"]
                raise DepsError(f"{name}: locked wheel without a sha256 hash ({url})")
            filename: str = ""
            if "name" in w:
                filename = w["name"]
            else:
                filename = url.rsplit("/", 1)[-1]
            wheels.append(LockedWheel(url, sha.lower(), filename))
        packages.append(
            LockedPackage(
                normalize_name(e["name"]),
                _version_string(e),
                tuple(wheels),
                SDIST if "sdist" in e else 0,
                e.get("marker"),
            )
        )
    return sorted(packages, key=lambda p: p.name)


def _version_string(entry: Dict[str, Any]) -> str:
    """The locked version, "" when the row omits it."""
    if "version" not in entry:
        return ""
    version: str = entry["version"]
    return str(version)


def lock_digest(text: str) -> str | None:
    data: Dict[str, Any] = _toml_loads(text)
    if "tool" not in data:
        return None
    tool_block: Dict[str, Any] = data["tool"]
    if LOCK_TOOL_KEY not in tool_block:
        return None
    tool: Dict[str, Any] = tool_block[LOCK_TOOL_KEY]
    if "input-digest" not in tool:
        return None
    value: str = tool["input-digest"]
    return value


def uv_binary() -> str:
    found = os.environ.get("PYSC_UV") or shutil.which("uv")
    if not found:
        raise DepsError("`uv` not found on PATH (set PYSC_UV); install it from https://docs.astral.sh/uv/")
    return found


@dataclass(frozen=True)
class UvResult:
    """What one uv run produced; `_run_uv` is the seam the tests replace.

    The compiler cannot lower `subprocess.CompletedProcess` (the generic
    annotation is not in the IR), so the seam carries a record with the
    three fields `_uv` reads; the tests build the same record shape with
    `subprocess.CompletedProcess` and the duck typing agrees.
    """

    returncode: int
    stderr: str
    stdout: str


def _run_uv(argv: list[str], cwd: Path) -> UvResult:
    """Seam for tests: every uv call goes through here."""
    done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
    return UvResult(done.returncode, done.stderr, done.stdout)


def _uv(args: list[str], cwd: Path) -> str:
    argv = [uv_binary()]
    for one in args:
        argv.append(one)
    proc = _run_uv(argv, cwd)
    if proc.returncode != 0:
        detail = proc.stderr.strip()
        if not detail:
            detail = proc.stdout.strip()
        hint = ""
        if "building from source is disabled" in detail or "--no-build" in detail:
            hint = "\nhint: a dependency publishes no wheel; pyscriptc never builds sdists (no setup.py execution)"
        raise DepsError(f"uv {args[0]} {args[1]} failed:\n{detail}{hint}")
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
    if not spec.all_requirements:
        text = 'lock-version = "1.0"\ncreated-by = "pyscriptc"\n'
    else:
        with tempfile.TemporaryDirectory(prefix="pyscriptc-deps-") as tmp:
            req = Path(tmp) / "requirements.in"
            out = Path(tmp) / "pylock.toml"
            req.write_text(spec.all_requirements + "\n", encoding="utf-8")
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
    py, abi, plat = wheel.tags()
    return abi == "none" and plat == "any" and _python_tag_ok(py, python or host_python())


def select_pure_wheel(pkg: LockedPackage, python: str | None = None) -> LockedWheel | None:
    pure = [w for w in pkg.wheels if is_pure_wheel(w, python)]
    return sorted(pure, key=lambda w: w.filename)[0] if pure else None


def _wheel_abis(pkg: LockedPackage) -> list[str]:
    """The distinct ABI tags of the package's wheels, sorted."""
    found: list[str] = []
    for wheel in pkg.wheels:
        abi = wheel.tags()[1]
        if abi not in found:
            found.append(abi)
    return sorted(found)


def classify_tags(pkg: LockedPackage, python: str | None = None) -> tuple[str, str]:
    """Lane and reason from wheel tags alone (no download)."""
    if select_pure_wheel(pkg, python) is not None:
        return "pure", "none-any wheel"
    if pkg.wheels:
        abis = ", ".join(sorted(_wheel_abis(pkg)))
        return "native", f"only ABI or platform specific wheels ({len(pkg.wheels)} wheels, ABI {abis})"
    if pkg.flags & SDIST:
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
        if len(parts) < 3 or parts[1] in ("scripts", "headers", "data"):
            return True
        parts = parts[2:]
        if not parts:
            return True
    if (len(parts) > 1 and parts[0] in ("test", "docs", "examples")) or SKIPPED_ANYWHERE in parts[:-1]:
        return True
    filename = parts[-1]
    if (filename.startswith("test_") and filename.endswith(".py")) or filename == "conftest.py":
        return True
    return False


def _is_code_member(parts: tuple[str, ...]) -> bool:
    """A wheel member the compiler can lower or refuses by lane, not data.

    Everything under a top-level directory that owns (or is) a Python
    module is code: the compiler reads it from the unpacked source root.
    A top-level `.py` file is a module too. Anything else the wheel's
    RECORD carries is data the compiled binary reads from the filesystem
    (`cacert.pem`, grammar files, `tzdata` zone tables).
    """

    if not parts or parts[-1].endswith((".py", ".pyc", ".pyi")):
        return True
    return f"{parts[0]}/__init__.py" in _CODE_ROOTS


# The top-level code roots of the wheel currently being read. `_should_skip_member`
# and `data_files` are wheel-scoped readers, so the root set is per wheel; both
# callers read it before iterating the archive. Thread safety: the compiler
# unpacks one wheel at a time inside `materialise`.
_CODE_ROOTS: set[str] = set()


def data_files(data: bytes) -> list[tuple[str, bytes]]:
    """The non-code members of a wheel, in RECORD order, paths kept wheel-relative.

    Issue #410: a pure wheel's data files (anything the RECORD marks that is
    neither a Python module nor inside a code package) are staged next to the
    emitted binary under the package's dotted path, so `importlib.resources`
    resolves in the compiled binary the way it does in CPython. Nothing here is
    executed and nothing is fetched at runtime: the bytes come from the same
    verified wheel the compiler reads the code from.
    """

    found: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        dist_infos = [n.split("/", 1)[0] for n in names if n.split("/", 1)[0].endswith(".dist-info")]
        _CODE_ROOTS.clear()
        if len(dist_infos) == 1:
            _CODE_ROOTS.update(_top_level_names(zf, names, dist_infos[0]))
        for info in zf.infolist():
            _safe_member(info)
            if info.is_dir():
                continue
            parts = PurePosixPath(info.filename).parts
            if _should_skip_member(parts) or _is_code_member(parts):
                continue
            if parts[0].endswith(".data"):
                parts = parts[2:]  # purelib/platlib content lands at the root
                if not parts or _is_code_member(parts):
                    continue
            found.append((info.filename, zf.read(info)))
    return found


def _top_level_names(zf: zipfile.ZipFile, names: list[str], dist_info: str) -> tuple[str, ...]:
    """The import names a wheel provides: `top_level.txt`, else RECORD's top level.

    `top_level.txt` is the authoritative mapping (issue #408). A wheel
    without it falls back to the top-level entries of its RECORD; a wheel
    with neither returns `()`, and the report says `unknown` rather than
    guessing from the distribution name.
    """

    top_level = f"{dist_info}/top_level.txt"
    if top_level in names:
        try:
            body = zf.read(top_level).decode("utf-8")
        except UnicodeDecodeError, zipfile.BadZipFile:
            body = ""
        return tuple(line.strip() for line in body.splitlines() if line.strip() and not line.strip().startswith("#"))
    found: list[str] = []
    for row in csv.reader(io.StringIO(zf.read(f"{dist_info}/RECORD").decode("utf-8"))):
        if not row or not row[0]:
            continue
        path = PurePosixPath(row[0])
        parts = path.parts
        if not parts or parts[0].endswith(".dist-info") or parts[0].endswith(".data"):
            continue
        # A top-level entry is a root `.py` module or a package directory
        # (it owns the `__init__.py` the unpack skips dist-info to record).
        if len(parts) == 1 and parts[0].endswith(".py"):
            name = parts[0][:-3]
        elif f"{parts[0]}/__init__.py" in names:
            name = parts[0]
        else:
            continue
        if name and name not in found:
            found.append(name)
    return tuple(found)


def _is_current_unpack(dest: Path) -> bool:
    marker = dest / ".complete"
    if not marker.is_file():
        return False
    try:
        lines = marker.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return len(lines) >= 2 and lines[1].strip() == str(UNPACK_SCHEMA_VERSION)


def _wheel_import_names(data: bytes) -> tuple[str, ...]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        dist_infos = {n.split("/", 1)[0] for n in names if n.split("/", 1)[0].endswith(".dist-info")}
        if len(dist_infos) == 1:
            return tuple(sorted(_top_level_names(zf, names, dist_infos.pop())))
    return ()


def materialise(data: bytes, sha256: str, cache: Path, *, name: str | None = None) -> Path:
    """Unpack a verified pure wheel into `src/<sha256>/` once; returns the import root."""
    dest = cache / "src" / sha256
    marker = dest / ".complete"
    if marker.is_file():
        if _is_current_unpack(dest):
            return dest
        top_level = list(_wheel_import_names(data))
        if top_level:
            (dest / ".imports").write_text("\n".join(top_level) + "\n", encoding="utf-8")
        marker.write_text(f"{sha256}\n{UNPACK_SCHEMA_VERSION}\n", encoding="utf-8")
        log.info(
            "deps.materialise",
            name=name or "",
            sha256=sha256,
            source_root=str(dest),
            skipped={"count": 0, "paths": []},
            importNames={"count": len(top_level), "names": top_level},
        )
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=dest.parent, prefix=".tmp-"))
    skipped: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
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
            dist_infos = {n.split("/", 1)[0] for n in names if n.split("/", 1)[0].endswith(".dist-info")}
            top_level = sorted(_top_level_names(zf, names, dist_infos.pop())) if len(dist_infos) == 1 else []
        (tmp / ".complete").write_text(f"{sha256}\n{UNPACK_SCHEMA_VERSION}\n", encoding="utf-8")
        if top_level:
            (tmp / ".imports").write_text("\n".join(top_level) + "\n", encoding="utf-8")
        if dest.exists():
            shutil.rmtree(dest)
        os.replace(tmp, dest)
        log.info(
            "deps.materialise",
            name=name or "",
            sha256=sha256,
            source_root=str(dest),
            skipped={"count": len(skipped), "paths": skipped},
            importNames={"count": len(top_level), "names": top_level},
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
    import_names: list[str] | None = None
    hint: str = ""

    def names(self) -> list[str]:
        """The import names, `[]` when none are recorded."""
        names = self.import_names
        if names is None:
            return []
        return names

    def __post_init__(self) -> None:
        self.hint = LANE_HINTS[self.lane]


def analyse(
    packages: list[LockedPackage], cache: Path, *, download: bool = True, offline: bool = False
) -> list[DepResult]:
    results: list[DepResult] = []
    for pkg in packages:
        lane, reason = classify_tags(pkg)
        result = DepResult(pkg.name, pkg.version, lane, reason, pkg.marker)
        wheel = select_pure_wheel(pkg)
        if wheel is not None:
            result.wheel = wheel.filename
            result.sha256 = wheel.sha256
            if download:
                data = fetch_wheel(wheel, cache, offline=offline)
                pure, reason = inspect_wheel(data)
                result.reason = reason
                if pure:
                    root = materialise(data, wheel.sha256, cache, name=pkg.name)
                    result.source_root = str(root)
                    result.import_names = list(unpacked_import_names(root))
                else:
                    result.lane = "native"
                    result.hint = LANE_HINTS["native"]
                    # A native wheel is never unpacked, but its names are
                    # still the mapping (issue #408): read them while the
                    # wheel is open here.
                    with zipfile.ZipFile(io.BytesIO(data)) as zf:
                        names = zf.namelist()
                        dist_infos = {n.split("/", 1)[0] for n in names if n.split("/", 1)[0].endswith(".dist-info")}
                        if len(dist_infos) == 1:
                            result.import_names = list(_top_level_names(zf, names, dist_infos.pop()))
        results.append(result)
    return results


def import_module_map(packages: list[DepResult]) -> dict[str, dict[str, str]]:
    """Import name to the distribution that provides it (issue #408).

    Keyed by the import name as recorded at unpack (`yaml`, `dateutil`),
    lowered, so a fence or a coverage entry on the import finds the
    distribution. Two distributions claiming one name is ambiguous: the
    first lock order wins and the entry keeps both names so the caller can
    say so. A pure distribution with no recorded names contributes one
    `unknown` entry keyed by its distribution-shaped module name, never a
    guess at the import names it might provide.
    """

    by_name: dict[str, list[DepResult]] = {}
    for result in packages:
        for import_name in result.names():
            key = import_name.lower()
            known = by_name.get(key)
            if known is None:
                by_name[key] = [result]
            else:
                current = known
                current.append(result)
    mapping: dict[str, dict[str, str]] = {}
    for import_name, providers in by_name.items():
        entry = _map_entry(providers[0])
        if len(providers) > 1:
            entry["ambiguous"] = ", ".join(sorted(p.name for p in providers))
        if len(providers[0].names()) > 1:
            entry["provides"] = ", ".join(sorted(providers[0].names()))
        mapping[import_name] = entry
    for result in packages:
        # Known import names are already in; a pure wheel with none of its
        # own is `unknown` keyed by its distribution-shaped module name, so
        # `import bare` still finds the locked distribution. A native or
        # sdist lane keeps its distribution-shaped key without the marker:
        # the fence names the lane, and there is no unpacked tree to lie
        # about.
        if result.names():
            continue
        module = result.name.lower().replace("-", "_").replace(".", "_")
        entry = _map_entry(result)
        if result.lane == "pure":
            entry["importNames"] = UNKNOWN_IMPORT_NAMES
        mapping.setdefault(module, entry)
    return mapping


def _map_entry(result: DepResult) -> dict[str, str]:
    return {
        "distribution": result.name,
        "lane": result.lane,
        "hint": result.hint,
    }


def unpacked_import_names(root: str | Path) -> tuple[str, ...]:
    """The recorded import names of one unpacked pure wheel, `()` when unknown.

    `materialise` writes the wheel's `top_level.txt` names (or its RECORD's
    top-level entries, issue #408) to `.imports` beside the unpacked
    source root; this reads it back.
    """

    try:
        body = (Path(root) / ".imports").read_text(encoding="utf-8")
    except OSError:
        return ()
    return tuple(line.strip() for line in body.splitlines() if line.strip())


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
    mapping = import_module_map(results)
    return {
        "source": str(spec.origin),
        "kind": spec.kind,
        "python": host_python(),
        "requiresPython": spec.requires_python,
        "dependencies": spec.requirement_list(),
        "lock": str(spec.uv_lock if provenance == "uv.lock" else spec.lock_path),
        "lockProvenance": provenance,
        "cache": str(cache),
        "lanes": dict(sorted(lanes.items())),
        "packages": [_report_row(r) for r in results],
        "sourceRoots": [r.source_root for r in results if r.source_root],
        "importNames": dict(sorted(mapping.items())),
    }


def _report_row(result: DepResult) -> Dict[str, Any]:
    """The report's per-distribution row: the record's fields as JSON scalars.

    The `Dict[str, Any]` container form is the self-host spell (the lowercase
    `dict[str, Any]` return annotation fences PS1106, like `mirlower.py`).
    The names row is a `list` copy, not a `tuple(...)`: the tuple constructor
    call is not lowered either, and a one-element or empty tuple literal
    fences. The JSON consumers read a list the same way they read a tuple.
    """
    row: Dict[str, Any] = {}
    row["name"] = result.name
    row["version"] = result.version
    row["lane"] = result.lane
    row["reason"] = result.reason
    row["wheel"] = result.wheel
    row["sha256"] = result.sha256
    row["source_root"] = result.source_root
    row["marker"] = result.marker
    row["import_names"] = list(result.names())
    row["hint"] = result.hint
    return row


def source_roots(source: Path, *, cache_dir: str | Path | None = None) -> list[Path]:
    """The unpacked pure-wheel roots a compile may import (issue #398).

    The deps report's `sourceRoots`, one directory per pure-Python
    dependency. The resolution reuses the lock and the content-addressed
    cache, so a second compile with the same lock does no network. A
    resolver failure is a compile-refusing PS3101; a dependency on any other
    lane simply contributes no root here, and the compile fences it with the
    lane hint the way it always has.
    """

    report = run_deps(source, download=True, cache_dir=cache_dir)
    return [Path(root) for root in report["sourceRoots"]]


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
        if p["lane"] == "pure":
            provided = p.get("import_names") or ()
            if provided:
                lines.append(f"  {'':<{width}}  {'':<6}  provides: {', '.join(provided)}")
            else:
                lines.append(
                    f"  {'':<{width}}  {'':<6}  provides: {UNKNOWN_IMPORT_NAMES} (no top_level.txt, no RECORD entries)"
                )
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


# ---------------------------------------------------------------- staging (issue #410)


def stage_data_files(
    data: list[tuple[str, list[tuple[str, bytes]]]],
    binary: Path,
) -> Path:
    """Stage the wheels' non-code files next to the emitted binary; returns the root.

    Issue #410: a pure wheel's data files (`cacert.pem`, zone tables, grammar
    files) must be on the compile-time path the compiled binary's
    `importlib.resources` searches, laid out under the package's dotted path
    (`certifi/cacert.pem`, `pkg/sub/data.bin`). The staging root is a sibling
    of the binary named `<binary>-data/`, written fresh on every compile (the
    old tree is removed first, so a recompile never leaves a stale file) and
    resolved to an absolute path: the binary's search path is a compile-time
    constant, so a relative root would break for any other working directory.
    Empty input stages nothing and returns the (not created) root: a package
    with no data files behaves exactly as before.
    """

    root = (binary.parent / (binary.name + "-data")).resolve()
    members = [(path, body) for _wheel, files in data for path, body in files]
    if not members:
        if root.exists():
            shutil.rmtree(root)
        return root
    tmp = Path(tempfile.mkdtemp(dir=root.parent, prefix=".tmp-data-"))
    try:
        for _wheel, files in data:
            for path, body in files:
                parts = PurePosixPath(path).parts
                target = tmp.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
        if root.exists():
            shutil.rmtree(root)
        os.replace(tmp, root)
        log.info("deps.stage_data", binary=str(binary), root=str(root), files=len(members))
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return root
