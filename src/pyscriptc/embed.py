"""The island runtime pack: lock file, fetch, cache and link resolution.

Everything here is boxed-only code (the ledger counts this module whole; see
`.agents/docs/boxed-cost.md` and `AGENTS.md`).

A runtime pack is a pinned CPython build (`python-build-standalone`) that an
island program links against instead of the host interpreter. The pack is
described by `packaging/runtime.lock.toml` (release tag, asset name, sha256),
produced by the release workflow job `runtime-pack` and fetched on demand by
`pyscriptc embed fetch` into `~/.cache/pyscriptc/runtime/<pack-id>/`.

The three modes of `--embed-python` (and of `[tool.pyscriptc.island] embed`):

- `host`: link the compiler's own interpreter. The link line is byte-identical
  to the pre-embed one.
- `bundle`: link the pinned runtime pack and write a relocatable output
  directory (`name/bin/name` plus `name/lib/`).
- `auto` (the default): `host` when the compiler interpreter has a usable
  shared libpython and headers, `bundle` otherwise. That is the Nuitka release
  binary case, where `sysconfig` describes the build machine and not the
  user's.

Offline and air-gapped use: `PYSC_RUNTIME_DIR` points at an unpacked pack
directory; the pack is then used as is, with no network access.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tarfile
import tempfile
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from pyscriptc import licenses, sitedeps, stdlibzip

MODES = ("host", "bundle", "auto")
DEFAULT_MODE = "auto"

# The pack host platform keys, as the lock file spells them.
_HOST_PLATFORM = {
    "Darwin": "macos",
    "Linux": "linux",
}


def host_platform() -> str:
    """The lock-file platform key for this machine ('macos-arm64', 'linux-x86_64')."""
    machine = os.uname().machine
    arch = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "x86_64",
        "amd64": "x86_64",
    }.get(machine)
    system = _HOST_PLATFORM.get(os.uname().sysname)
    if system is None or arch is None:
        return f"{os.uname().sysname}-{machine}"
    return f"{system}-{arch}"


def lock_path() -> Path:
    """The path of the runtime lock file in the installed package or the repo."""
    here = Path(__file__).resolve()
    for base in (here.parent.parent.parent, here.parent.parent):
        candidate = base / "packaging" / "runtime.lock.toml"
        if candidate.is_file():
            return candidate
    return here.parent.parent.parent / "packaging" / "runtime.lock.toml"


@dataclass(frozen=True)
class Pack:
    """One pinned runtime pack: the platform key, the asset and its hash."""

    platform: str
    cpython: str
    release: str
    asset: str
    sha256: str

    @property
    def pack_id(self) -> str:
        """The cache directory name: the CPython micro, the platform and the PBS tag."""
        return f"cp{self.cpython.replace('.', '')}-{self.platform}-{self.release}"

    @property
    def asset_url(self) -> str:
        return f"https://github.com/astral-sh/python-build-standalone/releases/download/{self.release}/{self.asset}"


class EmbedError(Exception):
    """A user-facing failure of the embedding path (no traceback)."""


def read_lock(path: Path | None = None) -> dict:
    """The parsed runtime lock file."""
    target = path or lock_path()
    try:
        with target.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise EmbedError(f"runtime lock file not found: {target}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise EmbedError(f"invalid runtime lock file {target}: {exc}") from exc


def lock_pack(platform: str | None = None, path: Path | None = None) -> Pack:
    """The pinned pack for a platform (the host platform by default)."""
    data = read_lock(path)
    release = str(data.get("release", ""))
    cpython = str(data.get("cpython", ""))
    runtime = data.get("runtime")
    if not isinstance(runtime, dict):
        raise EmbedError("runtime lock file has no [runtime] table")
    versions = runtime.get(cpython)
    if not isinstance(versions, dict):
        raise EmbedError(f"runtime lock file has no [runtime.{cpython}] table")
    key = platform or host_platform()
    entry = versions.get(key)
    if not isinstance(entry, dict):
        known = ", ".join(sorted(versions))
        raise EmbedError(f"no runtime pack for {key} (locked platforms: {known})")
    asset = entry.get("pbs_asset")
    sha256 = entry.get("sha256")
    if not isinstance(asset, str) or not isinstance(sha256, str):
        raise EmbedError(f"runtime lock entry {key} needs pbs_asset and sha256")
    return Pack(platform=key, cpython=cpython, release=release, asset=asset, sha256=sha256)


def sha256_file(path: Path) -> str:
    """The sha256 of a file, streamed."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
    """Refuse a file whose sha256 differs from the pinned one."""
    actual = sha256_file(path)
    if actual != expected:
        raise EmbedError(f"sha256 mismatch for {path.name}: expected {expected}, got {actual}")


def _download(url: str, dest: Path) -> None:
    try:
        with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 (a fixed https URL)
            with dest.open("wb") as handle:
                shutil.copyfileobj(response, handle)
    except urllib.error.URLError as exc:
        raise EmbedError(f"cannot download the runtime pack from {url}: {exc.reason}") from exc
    except OSError as exc:
        raise EmbedError(f"cannot write {dest}: {exc}") from exc


def unpack(archive: Path, dest: Path, only: tuple[str, ...] = ()) -> None:
    """Unpack a `.tar.zst` (or `.tar.gz`) archive into a directory.

    `only` keeps the members under the given path prefixes; the PBS Linux
    archives carry `share/terminfo` trees whose symlinks differ from their
    targets only in case, which a case-insensitive filesystem cannot extract,
    and nothing in a runtime pack needs `share/`.
    """
    try:
        with tarfile.open(archive, "r:*") as tar:
            _safe_extract(tar, dest, only)
    except tarfile.TarError as exc:
        raise EmbedError(f"cannot unpack {archive.name}: {exc}") from exc


def _wanted(name: str, only: tuple[str, ...]) -> bool:
    if not only:
        return True
    return any(name == prefix or name.startswith(prefix + "/") for prefix in only)


def _safe_extract(tar: tarfile.TarFile, dest: Path, only: tuple[str, ...] = ()) -> None:
    """Extract without letting a member escape the destination directory."""
    root = dest.resolve()
    members = [member for member in tar.getmembers() if _wanted(member.name, only)]
    for member in members:
        target = (root / member.name).resolve()
        if not str(target).startswith(str(root) + os.sep) and target != root:
            raise EmbedError(f"refusing the archive member {member.name!r}: it escapes the pack directory")
    tar.extractall(dest, members=members)  # noqa: S202 (every member was checked and filtered above)


def pack_root() -> Path:
    """The root of the runtime pack cache.

    `PYSC_CACHE_DIR` moves the whole cache root; the packs live in its
    `runtime/` subdirectory.
    """
    cache = os.environ.get("PYSC_CACHE_DIR")
    root = Path(cache) if cache else Path.home() / ".cache" / "pyscriptc"
    return root / "runtime"


def runtime_dir() -> Path | None:
    """The `PYSC_RUNTIME_DIR` override: an unpacked pack directory, or None."""
    value = os.environ.get("PYSC_RUNTIME_DIR")
    return Path(value) if value else None


def pack_dir(pack: Pack, root: Path | None = None) -> Path:
    """The unpacked pack directory for a pack.

    With `PYSC_RUNTIME_DIR` set, that directory is the pack itself (offline
    and air-gapped use): the variable names the unpacked runtime, whatever
    its own directory name is. Otherwise the pack lives in the cache under
    its pack id.
    """
    if root is None:
        override = runtime_dir()
        if override is not None:
            if list(override.glob("lib/libpython*")):
                return override
            return override / pack.pack_id
    return (root or pack_root()) / pack.pack_id


def pack_is_ready(pack: Pack, root: Path | None = None) -> bool:
    """True when the pack is already unpacked and usable."""
    directory = pack_dir(pack, root)
    return bool(list(directory.glob("lib/libpython*"))) and bool(list(directory.glob("include/python*")))


def fetch(pack: Pack, root: Path | None = None, *, force: bool = False) -> Path:
    """Download, verify and unpack a pack. Idempotent: an unpacked pack is reused.

    The download lands in a temporary file, is hashed against the pinned sha256
    and is refused when a byte differs; only then is it unpacked. With
    `PYSC_RUNTIME_DIR` set the override directory is used as is and never
    written to; fetching there is a no-op or an error.
    """
    if root is None and runtime_dir() is not None:
        directory = pack_dir(pack)
        if pack_is_ready(pack):
            return directory
        raise EmbedError(
            f"PYSC_RUNTIME_DIR={runtime_dir()} does not hold the {pack.pack_id} runtime; "
            "point it at an unpacked pack or unset it to use the cache"
        )
    base = root or pack_root()
    directory = pack_dir(pack, base)
    if pack_is_ready(pack, base) and not force:
        return directory
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base, prefix=".download-") as tmp:
        archive = Path(tmp) / pack.asset
        _download(pack.asset_url, archive)
        verify_sha256(archive, pack.sha256)
        staging = Path(tmp) / "unpacked"
        staging.mkdir()
        unpack(archive, staging)
        payload = _payload_root(staging)
        if directory.exists():
            shutil.rmtree(directory)
        shutil.move(str(payload), str(directory))
    return directory


def _payload_root(staging: Path) -> Path:
    """The pack root inside an unpacked archive.

    The python-build-standalone archives carry a `python/` prefix (and a
    `python/install/` prefix on some builds); the pack root is the directory
    that holds `include/` and `lib/`.
    """
    candidates = [staging]
    for prefix in ("", "*/", "*/ */"):
        prefix = prefix.replace(" ", "")
        if prefix:
            candidates.extend(sorted(staging.glob(prefix + "install")))
            candidates.extend(sorted(staging.glob(prefix + "python")))
        else:
            candidates.extend([staging / "install", staging / "python"])
    for candidate in candidates:
        if (candidate / "include").is_dir() and (candidate / "lib").is_dir():
            return candidate
    raise EmbedError("the runtime pack archive has no include/ and lib/ tree")


def ensure_pack(pack: Pack, root: Path | None = None, *, allow_download: bool = True) -> Path:
    """The unpacked pack directory, fetching it when it is missing.

    A compile in `bundle` mode needs the pack; it downloads it once (about 30
    MB) and reuses the cache afterwards. With no network the failure names
    `pyscriptc embed fetch` and `PYSC_RUNTIME_DIR`.
    """
    if pack_is_ready(pack, root):
        return pack_dir(pack, root)
    if not allow_download:
        raise EmbedError(
            f"the runtime pack {pack.pack_id} is not fetched yet; "
            f"run `pyscriptc embed fetch` or point PYSC_RUNTIME_DIR at an unpacked pack"
        )
    if runtime_dir() is None:
        print(
            f"fetching the runtime pack {pack.pack_id} (CPython {pack.cpython}, about 30 MB); "
            "run `pyscriptc embed fetch` to do it ahead of time",
            file=sys.stderr,
        )
    try:
        return fetch(pack, root)
    except EmbedError as exc:
        raise EmbedError(
            f"cannot fetch the runtime pack {pack.pack_id}: {exc}; "
            f"run `pyscriptc embed fetch` on a networked machine or point "
            f"PYSC_RUNTIME_DIR at an unpacked pack"
        ) from exc


def libpython(pack_directory: Path, version: str) -> Path:
    """The shared libpython of an unpacked pack."""
    libdir = pack_directory / "lib"
    mac = os.uname().sysname == "Darwin"
    name = f"libpython{version}.dylib" if mac else f"libpython{version}.so"
    candidate = libdir / name
    if not candidate.exists():
        matches = sorted(libdir.glob(f"libpython{version}.so*")) or sorted(libdir.glob(f"libpython{version}.dylib"))
        if not matches:
            raise EmbedError(f"the runtime pack has no libpython{version} in {libdir}")
        candidate = matches[0]
    return candidate


def host_has_shared_libpython() -> bool:
    """True when the running interpreter has a usable shared libpython and headers.

    A Nuitka onefile release binary has neither: `sysconfig` describes the
    build machine, so the island link would carry paths the user does not
    have. That is the case `auto` answers with `bundle`.
    """
    try:
        import sysconfig
    except ImportError:
        return False
    include = sysconfig.get_path("include")
    libdir = sysconfig.get_config_var("LIBDIR")
    version = sysconfig.get_config_var("VERSION")
    if not include or not libdir or not version:
        return False
    if not Path(include).is_dir():
        return False
    libdir_path = Path(libdir)
    mac = os.uname().sysname == "Darwin"
    name = f"libpython{version}.dylib" if mac else f"libpython{version}.so"
    return (libdir_path / name).exists()


def resolve_mode(mode: str | None) -> str:
    """Resolve the requested mode to `host` or `bundle`."""
    if mode is None or mode == "":
        mode = DEFAULT_MODE
    if mode not in MODES:
        raise EmbedError(f"--embed-python must be one of {'|'.join(MODES)}, got {mode!r}")
    if mode == "auto":
        return "host" if host_has_shared_libpython() else "bundle"
    return mode


def link_flags(pack_directory: Path, version: str) -> tuple[str, list[str]]:
    """The (include dir, link flags) of a bundle link.

    The library goes on the line by path, not as `-L`/`-l`: `zig cc` turns every
    `-L` directory into an absolute rpath, which would let a bundle run only
    because the build tree still exists. The relative rpath is the only one
    left, so the output directory is relocatable.
    """
    include = pack_directory / "include" / f"python{version}"
    if not include.is_dir():
        raise EmbedError(f"the runtime pack has no {include}")
    shared = libpython(pack_directory, version)
    mac = os.uname().sysname == "Darwin"
    origin = "@executable_path" if mac else "$ORIGIN"
    flags = [str(shared), f"-Wl,-rpath,{origin}/../lib"]
    if not mac:
        # The ELF link needs the pthread, dl, math and util objects that the
        # macOS link gets from the system libraries.
        flags += ["-ldl", "-lm", "-lpthread", "-lutil"]
    return str(include), flags


def stage_bundle(
    output: Path,
    pack_directory: Path,
    version: str,
    binary: Path,
    *,
    stdlib_mode: str = stdlibzip.DEFAULT_MODE,
    dependency_roots: list[str] | None = None,
) -> Path:
    """Copy the pack runtime next to the binary: `name/bin/name` plus `name/lib/`.

    The binary moves into `bin/`; the shared libpython goes into `lib/`, which
    is what the relative rpath and the getpath landmarks point at (a
    python-build-standalone `install/` tree is relocatable: it carries
    `@executable_path/../lib` already, so no install-name rewrite is needed).
    The stdlib is staged as a zip of `.pyc` bodies plus a real `lib-dynload/`
    directory (issue #659); the unpacked tree does not travel, and the pure-
    Python distributions behind `dependency_roots` are copied into its
    `site-packages/` (issue #660; a native dependency is refused by
    `sitedeps.check_pure` before staging begins). Headers stay in the pack: a
    relocated bundle runs, it does not compile.
    """
    out_dir = output if output.suffix == "" else output.parent / output.stem
    parent = out_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{out_dir.name}.bundle"
    shutil.rmtree(staging, ignore_errors=True)
    bin_dir = staging / "bin"
    lib_dir = staging / "lib"
    bin_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True)
    target = bin_dir / binary.name
    if binary.resolve() != target.resolve():
        shutil.move(str(binary), target)
    mac = os.uname().sysname == "Darwin"
    shared = libpython(pack_directory, version)
    if mac:
        shutil.copy2(shared, lib_dir / shared.name)
    else:
        # Linux ships libpython3.14.so as a symlink to libpython3.14.so.1.0; the
        # real file must travel with it or the loader finds nothing.
        for extra in sorted(shared.parent.glob(f"libpython{version}.so*")):
            shutil.copy2(extra, lib_dir / extra.name, follow_symlinks=False)
    stdlib_src = pack_directory / "lib" / f"python{version}"
    if stdlib_src.is_dir():
        stdlibzip.stage_stdlib(lib_dir, pack_directory, version, stdlib_mode)
    else:
        (lib_dir / f"python{version}" / "lib-dynload").mkdir(parents=True, exist_ok=True)
    if dependency_roots:
        sitedeps.stage_site_packages(lib_dir, version, dependency_roots)
    # Issue #661: the staged program data tree (issue #665) sits beside the
    # binary the compile produced, and the binary moves into `bin/`. The
    # island bootstrap resolves the tree from the running image (the binary's
    # own directory), so the tree has to travel into `bin/` with it or a
    # relocated bundle reads a directory that is gone.
    staged_data = binary.parent / "pyscriptc-data"
    if staged_data.is_dir():
        shutil.move(str(staged_data), str(bin_dir / "pyscriptc-data"))
    # Issue #709: the output redistributes a compiled CPython, so the license
    # texts travel with it - the runtime's own text from the pack, the
    # third-party texts vendored in the wheel, the NOTICE rollup, and the
    # licenses of whatever landed in site-packages.
    site_packages = lib_dir / f"python{version}" / "site-packages"
    licenses.stage_share_licenses(
        staging,
        pack_directory=pack_directory,
        version=version,
        site_packages=site_packages,
    )
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.move(str(staging), str(out_dir))
    return out_dir / "bin" / binary.name


def python_version() -> str:
    """The CPython version the compiler targets ('3.14')."""
    try:
        import sysconfig

        version = sysconfig.get_config_var("VERSION")
        if version:
            return str(version)
    except ImportError:
        pass
    return f"{sys.version_info.major}.{sys.version_info.minor}"
