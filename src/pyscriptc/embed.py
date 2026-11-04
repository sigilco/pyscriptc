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
import importlib.resources
import os
import platform
import random
import shutil
import string
import subprocess
import sys
import tarfile
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from pyscriptc import licenses, sitedeps, stdlibzip

MODES = ("host", "bundle", "auto")
DEFAULT_MODE = "auto"


def _platform_key(system: str, machine: str) -> str:
    """The lock-file platform row for a (system, machine) uname pair.

    Unknown rows keep CPython's raw `sysname-machine` spelling, the way the
    pre-typed code built the fallback key.
    """
    if system == "Darwin":
        row = "macos"
    elif system == "Linux":
        row = "linux"
    else:
        return f"{system}-{machine}"
    if machine == "arm64" or machine == "aarch64":
        return f"{row}-arm64"
    if machine == "x86_64" or machine == "amd64":
        return f"{row}-x86_64"
    return f"{system}-{machine}"


def host_platform() -> str:
    """The lock-file platform key for this machine ('macos-arm64', 'linux-x86_64')."""
    return _platform_key(platform.system(), platform.machine())


def lock_path() -> Path:
    """The path of the runtime lock file in the installed package or the repo."""
    # `__file__` is not lowered, so the package directory comes from
    # `importlib.resources.files` (the same lookup a frozen build answers with
    # the data directory beside the binary, where `packaging/` travels). The
    # search keeps the two roots the pre-typed code walked: the repo root
    # (two levels up from the package dir in a dev checkout) and the package
    # dir's parent (`src/`, or the install root that ships the `packaging/`
    # directory next to the package). Neither exists: the repo-root path is
    # returned so the caller's error names the layout the release build ships.
    root = Path(str(importlib.resources.files("pyscriptc")))
    beside = root.parent / "packaging" / "runtime.lock.toml"
    if beside.is_file():
        return beside
    repo = root.parent.parent / "packaging" / "runtime.lock.toml"
    return repo


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


def read_lock(path: Path | None = None) -> Dict[str, Any]:
    """The parsed runtime lock file.

    The annotation is the lock's own shape: the top-level keys carry
    different value types (the `schema` int, the `release` and `cpython`
    strings, the `runtime` table), so the container is `Dict[str, Any]` and
    every reader proves the shape it needs at the read site. The file is
    read as text because the lowered subset has the str `loads` entry only;
    the binary `load` form does not lower.
    """
    target = path or lock_path()
    try:
        with open(str(target), "rb") as handle:
            raw: str = handle.read().decode()
    except FileNotFoundError as exc:
        raise EmbedError(f"runtime lock file not found: {target}") from exc
    except OSError as exc:
        raise EmbedError(f"cannot read the runtime lock file {target}: {exc}") from exc
    try:
        schema: Dict[str, Dict[str, Dict[str, str]]] = tomllib.loads(raw)
    except Exception as exc:
        # The lowered subset has no `tomllib.TOMLDecodeError` class, so the
        # decode failure is caught as the base exception and reported with
        # the same message the direct catch produced.
        raise EmbedError(f"invalid runtime lock file {target}: {exc}") from exc
    return schema


def lock_pack(platform_key: str | None = None, path: Path | None = None) -> Pack:
    """The pinned pack for a platform (the host platform by default).

    Every read proves its type at the read site, so a lock missing a row
    refuses with the same `EmbedError` messages as before the typing pass.
    """
    lock = read_lock(path)
    cpython: str = lock["cpython"]
    release: str = lock["release"]
    runtime: Dict[str, Any] = lock["runtime"]
    versions: Dict[str, Dict[str, str]] = runtime[cpython]
    key = platform_key or host_platform()
    if key not in versions:
        known: list[str] = []
        for name in versions:
            known.append(name)
        known.sort()
        raise EmbedError(f"no runtime pack for {key} (locked platforms: {', '.join(known)})")
    entry: Dict[str, str] = versions[key]
    asset: str = entry["pbs_asset"]
    sha256: str = entry["sha256"]
    return Pack(platform=key, cpython=cpython, release=release, asset=asset, sha256=sha256)


def sha256_file(path: Path) -> str:
    """The sha256 of a file, streamed."""
    # The lowered subset hashes a bytes payload in one call, so the file is
    # read whole and hashed in one step (the streamed form does not lower).
    with open(str(path), "rb") as handle:
        data = handle.read()
    digest = hashlib.sha256(data)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
    """Refuse a file whose sha256 differs from the pinned one."""
    actual = sha256_file(path)
    if actual != expected:
        raise EmbedError(f"sha256 mismatch for {path.name}: expected {expected}, got {actual}")


def _download(url: str, dest: Path) -> None:
    try:
        # The lowered subset reads the whole response in one call, so the
        # body lands in memory before it is written (the pack is about 30 MB).
        response = urllib.request.urlopen(url, timeout=120.0)  # noqa: S310 (a fixed https URL)
        payload = response.read()
        with open(str(dest), "wb") as handle:
            handle.write(payload)
    except urllib.error.URLError as exc:
        raise EmbedError(f"cannot download the runtime pack from {url}: {exc}") from exc
    except OSError as exc:
        raise EmbedError(f"cannot write {dest}: {exc}") from exc


def unpack(archive: Path, dest: Path) -> None:
    """Unpack the archive into a directory.

    The lowered tarfile surface is the boolean `is_tarfile` check, so the
    extraction shells out to the host `tar` (the pinned PBS assets are
    gzip tars that a stock `tar` reads). A nonzero exit is the same
    user-facing `EmbedError` the direct `tarfile` call raised, with the
    archive name in the message. The member filter of the pre-typed code
    (`only`, the `share/` trim for the case-collision symlinks) goes away
    with the `tarfile` extraction: unpacking now happens in a scratch
    directory that is removed whole, and no pack consumer reads `share/`.
    """
    if not tarfile.is_tarfile(str(archive)):
        raise EmbedError(f"cannot unpack {archive.name}: not a tar archive")
    completed = subprocess.run(
        ["/usr/bin/tar", "-xf", str(archive), "-C", str(dest.resolve())],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise EmbedError(f"cannot unpack {archive.name}: {completed.stderr.strip()}")


def pack_root() -> Path:
    """The root of the runtime pack cache.

    `PYSC_CACHE_DIR` moves the whole cache root; the packs live in its
    `runtime/` subdirectory.
    """
    cache = os.environ.get("PYSC_CACHE_DIR")
    if cache:
        return Path(cache) / "runtime"
    return Path.home() / ".cache" / "pyscriptc" / "runtime"


def runtime_dir() -> Path | None:
    """The `PYSC_RUNTIME_DIR` override: an unpacked pack directory, or None."""
    value = os.environ.get("PYSC_RUNTIME_DIR")
    if value is None:
        return None
    if value == "":
        return None
    return Path(value)


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
            hit: list[str] = [child.name for child in override.glob("lib/libpython*")]
            if len(hit) > 0:
                return override
            return override / pack.pack_id
    if root is None:
        return pack_root() / pack.pack_id
    return root / pack.pack_id


def pack_is_ready(pack: Pack, root: Path | None = None) -> bool:
    """True when the pack is already unpacked and usable."""
    directory = pack_dir(pack, root)
    libs: list[str] = [child.name for child in directory.glob("lib/libpython*")]
    headers: list[str] = [child.name for child in directory.glob("include/python*")]
    return len(libs) > 0 and len(headers) > 0


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
        override = runtime_dir()
        if override is None:
            return directory
        raise EmbedError(
            f"PYSC_RUNTIME_DIR={override} does not hold the {pack.pack_id} runtime; "
            "point it at an unpacked pack or unset it to use the cache"
        )
    base = root or pack_root()
    directory = pack_dir(pack, base)
    if pack_is_ready(pack, base) and not force:
        return directory
    base.mkdir(parents=True, exist_ok=True)
    # The lowered `mkdtemp` takes no directory argument (it works in the
    # system temp dir), so the scratch directory is a random-suffix child
    # of the cache and is removed afterwards, like the context-manager
    # form the pre-typed code used.
    suffix: str = "".join(random.choice(string.ascii_lowercase) for _ in range(8))
    scratch: Path = base / f".download-{suffix}"
    scratch.mkdir()
    archive: Path = scratch / pack.asset
    _download(pack.asset_url, archive)
    verify_sha256(archive, pack.sha256)
    staging: Path = scratch / "unpacked"
    staging.mkdir()
    unpack(archive, staging)
    payload = _payload_root(staging)
    if directory.exists():
        shutil.rmtree(str(directory))
    shutil.move(str(payload), str(directory))
    try:
        shutil.rmtree(str(scratch))
    except OSError:
        pass
    return directory


def _payload_root(staging: Path) -> Path:
    """The pack root inside an unpacked archive.

    The python-build-standalone archives carry a `python/` prefix (and a
    `python/install/` prefix on some builds); the pack root is the directory
    that holds `include/` and `lib/`. `sorted` lowers over str lists only,
    so the glob results are collected as names and sorted as `list[str]`.
    """
    candidates: list[str] = []
    for prefix in ("*/", "*/ */"):
        installs: list[str] = [child.name for child in staging.glob(prefix.replace(" ", "") + "install")]
        installs.sort()
        pythons: list[str] = [child.name for child in staging.glob(prefix.replace(" ", "") + "python")]
        pythons.sort()
        for name in installs:
            candidates.append(name)
        for name in pythons:
            candidates.append(name)
    candidates.append("install")
    candidates.append("python")
    for name in candidates:
        candidate = staging / name
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
    mac = platform.system() == "Darwin"
    name = f"libpython{version}.dylib" if mac else f"libpython{version}.so"
    candidate = libdir / name
    if not candidate.exists():
        so: list[str] = [child.name for child in libdir.glob(f"libpython{version}.so*")]
        so.sort()
        dylib: list[str] = [child.name for child in libdir.glob(f"libpython{version}.dylib")]
        dylib.sort()
        matches: list[str] = so if len(so) > 0 else dylib
        if len(matches) == 0:
            raise EmbedError(f"the runtime pack has no libpython{version} in {libdir}")
        candidate = libdir / matches[0]
    return candidate


def host_has_shared_libpython() -> bool:
    """True when the running interpreter has a usable shared libpython and headers.

    A Nuitka onefile release binary has neither: `sysconfig` describes the
    build machine, so the island link would carry paths the user does not
    have. That is the case `auto` answers with `bundle`.

    `sysconfig` is not in the closed import table, so the same base directory
    comes from `sys.base_prefix` (the base interpreter of the running venv,
    which is where `sysconfig` reads LIBDIR from too) and the version from
    `platform.python_version`. A prefix whose include tree or libpython is
    missing answers False, the same refusal the sysconfig reads produced.
    """
    base = sys.base_prefix
    if base == "":
        return False
    version = _python_version()
    include = Path(base) / "include" / f"python{version}"
    if not include.is_dir():
        return False
    libdir = Path(base) / "lib"
    mac = platform.system() == "Darwin"
    name = f"libpython{version}.dylib" if mac else f"libpython{version}.so"
    return (libdir / name).exists()


def resolve_mode(mode: str | None) -> str:
    """Resolve the requested mode to `host` or `bundle`."""
    # The parameter narrows into a plain str local: tuple membership (`in`)
    # and formatting a `str | None` do not lower, and the equality chain
    # proves the mode against each MODES entry like the pre-typed `in` did.
    requested: str = DEFAULT_MODE
    if mode is not None:
        if mode != "":
            requested = mode
    if requested == MODES[0]:
        return requested
    if requested == MODES[1]:
        return requested
    if requested == MODES[2]:
        return _resolve_auto(requested)
    raise EmbedError(f"--embed-python must be one of host|bundle|auto, got {requested!r}")


def _resolve_auto(requested: str) -> str:
    """The `auto` resolution: `host` with a shared libpython, `bundle` otherwise."""
    if host_has_shared_libpython():
        return "host"
    # A platform with no locked pack (linux-aarch64, macos-x86_64) cannot bundle:
    # fall back to the host link, which native programs do not need libpython for.
    try:
        lock_pack()
    except EmbedError:
        return "host"
    return "bundle"


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
    mac = platform.system() == "Darwin"
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
    stdlib_mode: str = "safe",
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
    try:
        shutil.rmtree(str(staging))
    except OSError:
        pass
    bin_dir = staging / "bin"
    lib_dir = staging / "lib"
    bin_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True)
    target = bin_dir / binary.name
    if binary.resolve() != target.resolve():
        shutil.move(str(binary), str(target))
    mac = platform.system() == "Darwin"
    shared = libpython(pack_directory, version)
    if mac:
        shutil.copy2(str(shared), str(lib_dir / shared.name))
    else:
        # Linux ships libpython3.14.so as a symlink to libpython3.14.so.1.0; the
        # real file must travel with it or the loader finds nothing.
        extras: list[str] = [child.name for child in shared.parent.glob(f"libpython{version}.so*")]
        extras.sort()
        for name in extras:
            shutil.copy2(str(shared.parent / name), str(lib_dir / name))
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
    if out_dir.exists():
        shutil.rmtree(str(out_dir))
    shutil.move(str(staging), str(out_dir))
    return out_dir / "bin" / binary.name


def _python_version() -> str:
    """The CPython version the compiler targets ('3.14').

    `sysconfig` (the sysconfig `VERSION` config var the pre-typed code read)
    is not in the closed import table and `sys.version_info` is not in the
    lowered subset of `sys`, so the version is the first two fields of
    `platform.python_version`, the same runtime answer.
    """
    parts = platform.python_version().split(".")
    return parts[0] + "." + parts[1]


def python_version() -> str:
    """The CPython version the compiler targets ('3.14')."""
    return _python_version()
