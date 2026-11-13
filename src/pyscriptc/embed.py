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
from pyscriptc.target import TARGET_MINOR

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


def self_pack_dir() -> Path | None:
    """The runtime pack the running binary carries, when it carries one.

    A self-compiled pyscriptc (a `--embed-python=bundle` output) has the
    pinned runtime next to itself: `lib/libpythonX.Y.so`, `include/pythonX.Y`
    and `lib/pythonX.Y/` beside the binary. That tree is a pack, so a bundle
    compile of a user program can link against the runtime the binary
    already carries, with no cache and no network. The answer is None for
    an ordinary install (the pack lives in the cache).

    The staged program-data directory (`pyscriptc-data/` beside the binary)
    is the marker: an ordinary interpreter shares the same lib/include shape
    (a uv-managed python-build-standalone install has `lib/libpython*` and
    `include/python*` too) but no program-data directory, and its libpython
    is not the stripped pack build.
    """
    try:
        executable = Path(sys.executable).resolve()
    except OSError:
        return None
    root = executable.parent
    if not (root / "pyscriptc-data").is_dir():
        return None
    root = root.parent
    if not list((root / "lib").glob("libpython*")):
        return None
    if not list((root / "include").glob("python*")):
        return None
    return root


def pack_dir(pack: Pack, root: Path | None = None) -> Path:
    """The unpacked pack directory for a pack.

    With `PYSC_RUNTIME_DIR` set, that directory is the pack itself (offline
    and air-gapped use): the variable names the unpacked runtime, whatever
    its own directory name is. Otherwise the pack lives in the cache under
    its pack id. A binary that carries its own runtime (the self-compiled
    pyscriptc) uses that tree first: it is the same pinned runtime, already
    on disk.
    """
    if root is None:
        override = runtime_dir()
        if override is not None:
            hit: list[str] = [child.name for child in override.glob("lib/libpython*")]
            if len(hit) > 0:
                return override
            return override / pack.pack_id
        carried = self_pack_dir()
        if carried is not None:
            return carried
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
    the running interpreter's own `platform.python_version`. A prefix whose
    include tree or libpython is missing answers False, the same refusal the
    sysconfig reads produced.

    The version here is the *host's*, unlike `python_version()`: this probe
    asks what the machine that runs the compiler can link against, and a
    3.11 host has 3.11 headers, not 3.14 ones.
    """
    base = sys.base_prefix
    if base == "":
        return False
    version = ".".join(platform.python_version().split(".")[:2])
    include = Path(base) / "include" / f"python{version}"
    if not include.is_dir():
        return False
    libdir = Path(base) / "lib"
    mac = platform.system() == "Darwin"
    name = f"libpython{version}.dylib" if mac else f"libpython{version}.so"
    if (libdir / name).exists():
        return True
    # A relocatable build (a python-build-standalone install, the pinned
    # runtime pack) reports the build machine's LIBDIR (`/install/lib`),
    # which does not exist next to the running interpreter. The library
    # travels with the interpreter, so the interpreter's own directory is
    # the real answer.
    return (Path(sys.executable).resolve().parent.parent / "lib" / name).exists()


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


def strip_runtime_libs(lib_dir: Path) -> None:
    """Strip the staged libpython in place (`--strip-runtime`).

    Plain `strip` (not `--strip-debug`) removes the debug and the static
    symbol table but keeps `.dynsym`, which is where every `PyInit_*`
    extension entry point and the interpreter's own exported API live, so the
    interpreter still loads its built-in extension modules. About 12 MB off
    the 32 MB Linux libpython. A missing `strip` is not an error: the option
    is a size lever, not a correctness one.
    """
    if shutil.which("strip") is None:
        return
    for shared in sorted(lib_dir.glob("libpython*")):
        if shared.is_symlink() or not shared.is_file():
            continue
        subprocess.run(["strip", str(shared)], check=False)


def stage_bundle(
    output: Path,
    pack_directory: Path,
    version: str,
    binary: Path,
    *,
    stdlib_mode: str = "safe",
    dependency_roots: list[str] | None = None,
    with_headers: bool = False,
    strip_runtime: bool = False,
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
        # Linux ships libpython3.14.so as a symlink to libpython3.14.so.1.0.
        # The real file must travel with it or the loader finds nothing, but
        # the unversioned name is only a link: copying it dereferenced
        # duplicated the 32 MB library in every bundle (issue #885). Copy
        # the real file once and recreate the link.
        extras: list[str] = [child.name for child in shared.parent.glob(f"libpython{version}.so*")]
        extras.sort()
        for name in extras:
            source = shared.parent / name
            if source.is_symlink():
                (lib_dir / name).symlink_to(source.readlink())
            else:
                shutil.copy2(str(source), str(lib_dir / name))
    if strip_runtime:
        strip_runtime_libs(lib_dir)
    stdlib_src = pack_directory / "lib" / f"python{version}"
    if stdlib_src.is_dir():
        stdlibzip.stage_stdlib(lib_dir, pack_directory, version, stdlib_mode)
    else:
        (lib_dir / f"python{version}" / "lib-dynload").mkdir(parents=True, exist_ok=True)
    if with_headers:
        # A bundle that is itself a compiler (the self-compiled pyscriptc)
        # compiles island programs at run time, and that compile needs the
        # CPython headers: sysconfig of the bundled interpreter resolves
        # `include` inside this directory tree.
        include_src = pack_directory / "include"
        if include_src.is_dir():
            shutil.copytree(include_src, staging / "include")
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
    """The CPython minor the emitted program claims ('3.14').

    `sysconfig` (the sysconfig `VERSION` config var the pre-typed code read)
    is not in the closed import table and `sys.version_info` is not in the
    lowered subset of `sys`, so the version used to be the host's
    `platform.python_version`. That leaks the tool host into the program: a
    3.11 host would look for `python3.11` inside the pinned 3.14 pack. The
    answer is the declared target (`target.json`), which is the same value
    the runtime lock pins (`cpython = "3.14.9"`).
    """
    return TARGET_MINOR


def python_version() -> str:
    """The CPython minor the emitted program claims ('3.14')."""
    return _python_version()


# boxed:begin
# The PyPy runtime pack (issue #668): a pinned PyPy release from
# downloads.python.org, described by `[pypy]` rows of the same runtime lock
# file. The pack shape mirrors the CPython one (`include/` + `lib/`, a
# relative rpath the bundle keeps relocatable), with the PyPy naming: the
# shared library is `libpypy<lang>-c.so` / `.dylib`, the stdlib tree is
# `lib/pypy<lang>/`, and `pypy_setup_home(NULL)` anchors the runtime on the
# loaded library's own path, so the same layout works from anywhere.
#
# Unlike the CPython pack, the PyPy pack is opt-in through the engine
# descriptor (`--island-engine`): the lock row is read only when a
# PyPy-kind engine is selected, so the default path never touches it.
PYPY_LOCK_SCHEMA = 1
PYPY_DOWNLOADS = "https://downloads.python.org/pypy"


@dataclass(frozen=True)
class PyPyPack:
    """One pinned PyPy pack: the platform key, the release and its hash."""

    platform: str
    version: str
    python: str
    asset: str
    sha256: str

    @property
    def pack_id(self) -> str:
        """The cache directory name: the PyPy release, the platform."""
        return f"pypy{self.version}-{self.platform}-py{self.python}"

    @property
    def asset_url(self) -> str:
        return f"{PYPY_DOWNLOADS}/{self.asset}"


def read_pypy_lock(path: Path | None = None) -> Dict[str, Any]:
    """The `[pypy]` table of the runtime lock file.

    A lock with no `[pypy]` table (an older lock file) reads as an empty
    table: a PyPy selection then fails on the missing row, with the same
    message shape as an unlocked CPython platform. The file is read as text
    for the same reason `read_lock` does (the lowered subset has the str
    `loads` entry only).
    """
    lock = read_lock(path)
    table: Any = lock.get("pypy")
    if not isinstance(table, dict):
        empty: Dict[str, Any] = {}
        return empty
    return table


def lock_pypy_pack(platform_key: str | None = None, path: Path | None = None) -> PyPyPack:
    """The pinned PyPy pack for a platform (the host platform by default).

    The lock row carries the PyPy release (`version`, the `7.3.x` the
    asset name embeds), the language level (`python`, the `3.x` the
    library name embeds), the asset file name and its sha256. A missing
    platform row or a lock whose `schema` is newer than the one this
    compiler reads refuses with an `EmbedError` that names the row.
    """
    table = read_pypy_lock(path)
    schema: Any = table.get("schema")
    if isinstance(schema, int) and schema > PYPY_LOCK_SCHEMA:
        raise EmbedError(f"the runtime lock's pypy schema {schema} is newer than the supported {PYPY_LOCK_SCHEMA}")
    versions: Any = table.get("releases")
    if not isinstance(versions, dict) or not versions:
        raise EmbedError("the runtime lock has no pypy releases pinned")
    key = platform_key or host_platform()
    row: Any = versions.get(key)
    if not isinstance(row, dict):
        known: list[str] = [name for name in versions if isinstance(name, str)]
        known.sort()
        raise EmbedError(f"no pypy runtime pack for {key} (locked platforms: {', '.join(known)})")
    version: str = row["version"]
    python: str = row["python"]
    asset: str = row["asset"]
    sha256: str = row["sha256"]
    for label, value in (("version", version), ("python", python), ("asset", asset), ("sha256", sha256)):
        if not isinstance(value, str) or not value:
            raise EmbedError(f"the runtime lock's pypy {key} row has no valid {label}")
    return PyPyPack(platform=key, version=version, python=python, asset=asset, sha256=sha256)


def pypy_pack_dir(pack: PyPyPack, root: Path | None = None) -> Path:
    """The unpacked PyPy pack directory for a pack.

    The same rule as `pack_dir`: `PYSC_RUNTIME_DIR` names an unpacked pack
    directory used as is (offline use), otherwise the cache root child.
    """
    if root is None:
        override = runtime_dir()
        if override is not None:
            return override
        return pack_root() / pack.pack_id
    return root / pack.pack_id


def pypy_pack_is_ready(pack: PyPyPack, root: Path | None = None) -> bool:
    """True when the PyPy pack is already unpacked and usable."""
    directory = pypy_pack_dir(pack, root)
    return pypy_library(directory, pack.python) is not None


def fetch_pypy(pack: PyPyPack, root: Path | None = None, *, force: bool = False) -> Path:
    """Download, verify and unpack a PyPy pack. Idempotent like `fetch`.

    The PyPy archives unpack to `pypy3.11-v7.3.x-<platform>/`; the pack
    directory is that payload root moved into the cache, so `include/` and
    `lib/` sit at its top the way the CPython pack's do.
    """
    if root is None and runtime_dir() is not None:
        directory = pypy_pack_dir(pack)
        if pypy_pack_is_ready(pack):
            return directory
        override = runtime_dir()
        if override is None:
            return directory
        raise EmbedError(
            f"PYSC_RUNTIME_DIR={override} does not hold the {pack.pack_id} runtime; "
            "point it at an unpacked pack or unset it to use the cache"
        )
    base = root or pack_root()
    directory = pypy_pack_dir(pack, base)
    if pypy_pack_is_ready(pack, base) and not force:
        return directory
    base.mkdir(parents=True, exist_ok=True)
    suffix: str = "".join(random.choice(string.ascii_lowercase) for _ in range(8))
    scratch: Path = base / f".download-{suffix}"
    scratch.mkdir()
    archive: Path = scratch / pack.asset
    _download(pack.asset_url, archive)
    verify_sha256(archive, pack.sha256)
    staging: Path = scratch / "unpacked"
    staging.mkdir()
    unpack(archive, staging)
    payload = _pypy_payload_root(staging)
    if directory.exists():
        shutil.rmtree(str(directory))
    shutil.move(str(payload), str(directory))
    try:
        shutil.rmtree(str(scratch))
    except OSError:
        pass
    return directory


def ensure_pypy_pack(pack: PyPyPack, root: Path | None = None, *, allow_download: bool = True) -> Path:
    """The unpacked PyPy pack directory, fetching it when it is missing."""
    if pypy_pack_is_ready(pack, root):
        return pypy_pack_dir(pack, root)
    if not allow_download:
        raise EmbedError(
            f"the runtime pack {pack.pack_id} is not fetched yet; "
            f"run `pyscriptc embed fetch` or point PYSC_RUNTIME_DIR at an unpacked pack"
        )
    if runtime_dir() is None:
        print(
            f"fetching the pypy runtime pack {pack.pack_id} (PyPy {pack.version}, about 60 MB); "
            "run `pyscriptc embed fetch` to do it ahead of time",
            file=sys.stderr,
        )
    try:
        return fetch_pypy(pack, root)
    except EmbedError as exc:
        raise EmbedError(
            f"cannot fetch the runtime pack {pack.pack_id}: {exc}; "
            f"run `pyscriptc embed fetch` on a networked machine or point "
            f"PYSC_RUNTIME_DIR at an unpacked pack"
        ) from exc


def _pypy_payload_root(staging: Path) -> Path:
    """The pack root inside an unpacked PyPy archive.

    The PyPy release archives carry a single `pypy<lang>-v<release>-<platform>/`
    prefix directory (no `install/` layer like the PBS assets); the pack root
    is the one child that holds `include/` and `lib/`.
    """
    children: list[str] = [child.name for child in staging.iterdir() if child.is_dir()]
    children.sort()
    for name in children:
        candidate = staging / name
        if (candidate / "include").is_dir() and (candidate / "lib").is_dir():
            return candidate
    raise EmbedError("the pypy runtime pack archive has no include/ and lib/ tree")


def pypy_library(pack_directory: Path, python: str) -> Path | None:
    """The shared `libpypy<lang>-c` library of an unpacked pack, or None.

    The Linux name is `libpypy3.11-c.so`, the macOS one
    `libpypy3.11-c.dylib`; both live in `bin/` of the unpacked release (the
    directory the interpreter binary sits in), not in `lib/`. The stage
    moves it into the bundle's `lib/`, so the caller never depends on where
    the unpacked release kept it.
    """
    mac = platform.system() == "Darwin"
    suffix = "dylib" if mac else "so"
    name = f"libpypy{python}-c.{suffix}"
    for parent in (pack_directory / "bin", pack_directory / "lib"):
        candidate = parent / name
        if candidate.is_file():
            return candidate
    return None


def pypy_link_flags(pack_directory: Path, python: str) -> tuple[str, list[str]]:
    """The (include dir, link flags) of a PyPy island link against the pack.

    Two differences from the CPython bundle line, both measured on the real
    pack (issue #668):

    - The library goes on as `-L <dir> -l pypy<lang>-c` **by soname**, not
      by path. A path-named entry lands in the binary's `NEEDED` list
      verbatim (the linker records the string it was given), and a
      non-absolute name breaks on the first directory move; the soname form
      resolves through the relative rpath after the bundle is relocated.
    - The rpath is `$ORIGIN/../lib`, and the portable libraries the pack
      ships travel in that same directory. `libpypy<lang>-c.so` carries
      `NEEDED` entries for them (`libtinfow`, `libsqlite3`, `libz`, ...)
      and its own `RUNPATH` is `$ORIGIN/../lib`, so a staged copy resolves
      next to the library itself. The bundle therefore runs on a host that
      has none of them (measured: `ldd` resolves every one of them out of
      the bundle's own `lib/`).
    """
    include = pack_directory / "include" / f"pypy{python}"
    if not include.is_dir():
        raise EmbedError(f"the pypy runtime pack has no {include}")
    shared = pypy_library(pack_directory, python)
    if shared is None:
        raise EmbedError(f"the pypy runtime pack has no libpypy{python}-c in {pack_directory}")
    mac = platform.system() == "Darwin"
    origin = "@executable_path" if mac else "$ORIGIN"
    flags = [f"-L{shared.parent}", f"-lpypy{python}-c", f"-Wl,-rpath,{origin}/../lib"]
    if not mac:
        flags += ["-ldl", "-lm", "-lpthread", "-lutil"]
    return str(include), flags


def stage_pypy_bundle(
    output: Path,
    pack_directory: Path,
    python: str,
    binary: Path,
) -> Path:
    """Copy the PyPy pack runtime next to the binary: `name/bin/name` + `name/lib/`.

    The same output shape the CPython bundle stages (`bin/` for the binary,
    `lib/` for the runtime and its libraries, the relative rpath pointing at
    `lib/`), with the PyPy tree inside: the shared `libpypy<lang>-c` plus
    every portable library the release ships (its `NEEDED` list names them
    all; the library's own `RUNPATH` resolves them from `lib/` on a
    relocated run) and the `lib/pypy<lang>/` stdlib tree whole. Headers
    stay in the pack: a relocated island runs, it does not compile.
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
    shared = pypy_library(pack_directory, python)
    if shared is None:
        raise EmbedError(f"the pypy runtime pack has no libpypy{python}-c in {pack_directory}")
    lib_dir.mkdir(parents=True, exist_ok=True)
    # The runtime library first, then the portable libraries it needs: the
    # archive's lib/ holds them (libtinfow, libsqlite3, libz, ...). They
    # travel in `lib/` next to the library itself, which is where the
    # library's own `RUNPATH` looks for them. A symlink in the release
    # stays a symlink if its target travels too (PyPy's Linux archives
    # ship plain files, the rule matches the CPython symlink handling for
    # a future release that does not).
    shutil.copy2(str(shared), str(lib_dir / shared.name))
    for child in sorted(pack_directory.glob("lib/*.so*") if not mac else pack_directory.glob("lib/*.dylib")):
        if child.is_symlink():
            (lib_dir / child.name).symlink_to(child.readlink())
        elif child.is_file():
            shutil.copy2(str(child), str(lib_dir / child.name))
    # The stdlib tree (`lib/pypy3.11/`), whole: no trim for v1 (the PyPy
    # tree has no `.pyc`-only layout the CPython zip staging relies on, and
    # the engine's own stdlib is what the manifest declares).
    stdlib_src = pack_directory / "lib" / f"pypy{python}"
    if stdlib_src.is_dir():
        shutil.copytree(str(stdlib_src), str(lib_dir / f"pypy{python}"), symlinks=True)
    if out_dir.exists():
        shutil.rmtree(str(out_dir))
    shutil.move(str(staging), str(out_dir))
    return out_dir / "bin" / binary.name


# boxed:end
