"""The bundled stdlib of a runtime pack: a safe-trimmed zip of `.pyc`.

Everything here is boxed-only code (the ledger counts this module whole; see
`.agents/docs/boxed-cost.md` and `AGENTS.md`).

A `--embed-python=bundle` program runs against the pack's CPython, but the
`lib/python3.14/` tree of a pack is 10 MB of `.py` files the interpreter would
compile at first import. `stage_stdlib` writes it as a single zip of
byte-compiled modules instead, plus a real `lib-dynload/` directory (getpath
prints a fatal-looking warning without one, and PBS builds most extension
modules into libpython so the directory is near-empty).

Two trims, both shipped as zips:

- `safe` (the default): everything except a documented deny list of trees no
  compiled program or island import needs (`test`, `idlelib`, `tkinter`,
  `turtledemo`, `ensurepip`, `pydoc_data`, `__phello__`). The trim is safe
  because it removes only developer-facing material; anything else an island
  program imports must be present, and `stdlib_manifest` fails the compile
  closed (`PS4301`) when it is not.
- `minimal` (opt-in, `--embed-stdlib=minimal`): a frozen closure over the
  CPython import graph seeded from `PYTHONSTARTUP_MODULES` (the island floor:
  `encodings`, `codecs`, `os` and friends) plus an explicit allowlist
  (`MINIMAL_EXTRA`). It exists for size-critical bundles; a missing module
  still fails closed at compile time, so the failure is a build error, not a
  runtime `ModuleNotFoundError`.

The zip stores `.pyc` bodies with their magic-number header; the archive is
deflated, because the zip is data (not an executable) and the saved bytes
cost one decompression of a few milliseconds at first import. A stored
(uncompressed) zip keeps the pages cache-friendly, and the pack reproducible
byte-for-byte from the same pack directory either way.
"""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import os
import py_compile
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

STDLIB_MODES = ("safe", "minimal")
DEFAULT_MODE = "safe"

# The documented safe-trim deny list: top-level stdlib trees and modules no
# compiled program or island import needs. `test` is not even present in the
# `install_only_stripped` archives, but a pack built from another asset may
# carry it. This list is the whole contract of the safe trim; grow it only with
# a documented reason, never to chase a smaller zip at correctness' expense.
SAFE_TRIM = (
    "__phello__",
    "idlelib",
    "ensurepip",
    "pydoc_data",
    "test",
    "tkinter",
    "turtledemo",
)

# The frozen closure seed of the minimal trim: what an island bootstrap needs
# before the first program import (the frozen importlib pulls these).
MINIMAL_SEED = (
    "_collections_abc",
    "_compat_pickle",
    "_compression",
    "_sitebuiltins",
    "_weakrefset",
    "abc",
    "codecs",
    "collections",
    "contextlib",
    "copyreg",
    "encodings",
    "enum",
    "functools",
    "genericpath",
    "heapq",
    "importlib",
    "io",
    "itertools",
    "keyword",
    "linecache",
    "operator",
    "os",
    "posixpath",
    "re",
    "reprlib",
    "site",
    "sre_compile",
    "sitecustomize",
    "stat",
    "types",
    "typing",
    "warnings",
    "weakref",
    "zipfile",
)

# Modules the minimal trim keeps unconditionally even though the seed closure
# does not reach them: the island bootstrap (`importlib.abc`, `importlib.util`,
# `json` in tests, `pathlib` in everyday code) and `site`'s dependencies.
MINIMAL_EXTRA = (
    "_blake2",
    "_bootstrap_lite",
    "collections.abc",
    "concurrent",
    "configparser",
    "contextvars",
    "copy",
    "dataclasses",
    "datetime",
    "difflib",
    "dis",
    "fnmatch",
    "fractions",
    "gc",
    "glob",
    "hashlib",
    "hmac",
    "inspect",
    "json",
    "locale",
    "logging",
    "numbers",
    "pathlib",
    "pickle",
    "platform",
    "pprint",
    "random",
    "shutil",
    "socket",
    "socketserver",
    "ssl",
    "string",
    "struct",
    "subprocess",
    "sysconfig",
    "tempfile",
    "textwrap",
    "threading",
    "traceback",
    "types",
    "unittest",
    "urllib",
    "uuid",
    "argparse",
    "base64",
    "bisect",
    "bz2",
    "cmath",
    "contextlib",
    "csv",
    "getopt",
    "getpass",
    "gzip",
    "email",
    "_markupbase",
    "importlib.resources",
    "importlib.resources._common",
    "importlib.resources._adapters",
    "importlib.resources._functional",
    "importlib.resources._itertools",
    "importlib.resources._legacy",
    "importlib.metadata",
    "importlib.metadata._adapters",
    "importlib.metadata._collections",
    "importlib.metadata._functools",
    "importlib.metadata._itertools",
    "importlib.metadata._text",
    "importlib.abc",
    "importlib.readers",
    "encodings.idna",
    "fileinput",
    "optparse",
    "pty",
    "secrets",
    "html",
    "http",
    "ipaddress",
    "lzma",
    "queue",
    "shlex",
    "signal",
    "sqlite3",
    "statistics",
    "tarfile",
    "timeit",
    "tomllib",
    "unittest.mock",
    "uuid",
    "xml",
    "xml.etree",
    "xml.etree.ElementTree",
    "zlib",
)


class StdlibError(Exception):
    """A user-facing stdlib bundle failure (no traceback)."""


def safe_trim_modules(stdlib_dir: Path) -> list[Path]:
    """Every `.py` of a stdlib tree that the safe trim keeps.

    The deny list matches top-level names only: `tkinter` drops the package
    (and with it `turtledemo`'s only consumer), not a same-named submodule of
    an unrelated package. The pack's `site-packages/` is out too: it holds
    the pack interpreter's own tooling (pip), never a program dependency
    (issue #885); program dependencies travel through `sitedeps`.
    """
    if not stdlib_dir.is_dir():
        raise StdlibError(f"the runtime pack has no stdlib tree at {stdlib_dir}")
    kept: list[Path] = []
    for path in sorted(stdlib_dir.rglob("*.py")):
        relative = path.relative_to(stdlib_dir)
        if relative.parts[0] in SAFE_TRIM:
            continue
        if relative.parts[0] == "site-packages":
            continue
        kept.append(path)
    return kept


def _import_names(source: str) -> set[str]:
    """The absolute import names of a module source, ignoring relatives."""

    found: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def _import_names_relative(source: str, package: str) -> set[str]:
    """The import names of a module source, relatives resolved to absolute.

    A `from . import sibling` inside a package resolves against the package,
    and the frozen closure must follow it or a kept submodule loses a peer
    (`zipfile` pulls `_path` that way). A `from pkg import submod` names the
    submodule only through the alias, so `pkg.submod` joins the candidates
    too (`email._policybase` pulls `email.header` that way); names that are
    not modules simply find no file in the closure walk.
    """

    found = _import_names(source)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            module = node.module or ""
            found.update(f"{module}.{alias.name}" for alias in node.names if alias.name != "*" and module)
            continue
        parts = package.split(".") if package else []
        if node.level > 1:
            parts = parts[: len(parts) - (node.level - 1)]
        base = ".".join(parts)
        if node.module:
            found.add(f"{base}.{node.module}" if base else node.module)
            base = f"{base}.{node.module}" if base else node.module
        for alias in node.names:
            if alias.name != "*":
                found.add(f"{base}.{alias.name}" if base else alias.name)
    return found


def _stdlib_available() -> Path:
    """A stdlib tree to read sources from: the running interpreter's."""
    candidate = Path(sys.base_prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}"
    if candidate.is_dir():
        return candidate
    for entry in sys.path:
        if entry.endswith("-packages") or not entry:
            continue
        base = Path(entry)
        if (base / "os.py").is_file():
            return base
    raise StdlibError("cannot locate a stdlib source tree for the minimal closure")


def minimal_modules(stdlib_dir: Path | None = None) -> list[Path]:
    """The minimal-trim module list: a frozen closure, not a runtime one.

    The closure walks the import graph of the seed plus allowlist sources
    breadth-first over the stdlib tree of the running interpreter (the compiler
    host and the pack share the 3.14 stdlib layout), one pass per module, and
    then maps the kept names back to the pack's own files. Frozen means the
    answer depends on these two tuples and the 3.14 sources only, so two
    compilers agree without executing anything.
    """
    source_tree = stdlib_dir if stdlib_dir is not None and (stdlib_dir / "os.py").is_file() else _stdlib_available()
    # A seed package travels whole (`encodings` pulls its codec and alias
    # modules through dynamic imports no static walk sees); a seed module
    # travels alone and only grows through the closure below.
    keep = set()
    queue: list[str] = []
    for name in (*MINIMAL_SEED, *MINIMAL_EXTRA):
        parts = name.split("/")
        package = source_tree.joinpath(*parts)
        if package.is_dir() and (package / "__init__.py").is_file():
            keep.add(name)
            for child in sorted(package.glob("*.py")):
                keep.add(f"{name}.{child.stem}")
        queue.append(name)
    # `sysconfig` reads the build's configuration through a data module whose
    # name carries the platform and the ABI flags (`_sysconfigdata__linux_
    # x86_64-linux-gnu`), imported by name at run time. The static walk
    # cannot see it, so the module travels whenever `sysconfig` does.
    for data in sorted(source_tree.glob("_sysconfigdata_*.py")):
        keep.add(data.stem)
    seen: set[str] = set()
    while queue:
        name = queue.pop(0)
        if name in seen:
            continue
        seen.add(name)
        if name.split(".")[0] in SAFE_TRIM:
            # The deny list is out of every trim: `pydoc` pulls `pydoc_data`
            # through a function-body import the island never executes.
            continue
        path = source_tree / f"{name.replace('.', '/')}.py"
        if not path.is_file():
            path = source_tree / name.replace(".", "/") / "__init__.py"
        if not path.is_file():
            continue
        keep.add(name)
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        for found in _import_names_relative(path.read_text(encoding="utf-8", errors="replace"), package):
            if found in seen:
                continue
            parts = found.split(".")
            # Only stdlib names continue the closure; a third-party or unknown
            # import cannot be satisfied from the stdlib tree anyway.
            candidate = source_tree / f"{parts[0]}.py"
            if not candidate.is_file() and not (source_tree / parts[0]).is_dir():
                continue
            queue.append(found)
            for cut in range(1, len(parts) + 1):
                prefix = ".".join(parts[:cut])
                if prefix.split(".")[0] not in SAFE_TRIM:
                    keep.add(prefix)
                    # A parent package's `__init__` runs on the submodule
                    # import, so its own imports belong to the closure too
                    # (`importlib.resources.__init__` pulls `._common`).
                    queue.append(prefix)
    tree = stdlib_dir if stdlib_dir is not None else source_tree
    files: list[Path] = []
    seen: set[Path] = set()
    for name in sorted(keep):
        rel = name.replace(".", "/")
        for candidate in (tree / f"{rel}.py", tree / rel / "__init__.py"):
            if candidate.is_file():
                # A package's `__init__.py` answers two names (the package
                # and the module), so the same file can be reached twice. The
                # zip would then carry the entry twice, which the archive
                # writer warns about and the importer reads twice.
                if candidate not in seen:
                    seen.add(candidate)
                    files.append(candidate)
                break
    return files


def kept_module_names(sources: list[Path], stdlib_dir: Path | None = None) -> set[str]:
    """The dotted module names a source list keeps, derived from its paths.

    Every package level of `email/mime/text.py` counts (`email`,
    `email.mime`, `email.mime.text`), so the manifest check can answer a
    submodule import without unpacking anything.
    """

    base = stdlib_dir
    if base is None and sources:
        # Every source sits under the same stdlib root; recover it from the
        # first path (`.../lib/python3.14/os.py`).
        parts = sources[0].parts
        for cut in range(len(parts) - 1, -1, -1):
            if parts[cut].startswith("python") and parts[cut][6:].replace(".", "").isdigit():
                base = Path(*parts[: cut + 1])
                break
    if base is None:
        return set()
    found: set[str] = set()
    for source in sources:
        try:
            relative = source.relative_to(base)
        except ValueError:
            continue
        parts = list(relative.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        for cut in range(1, len(parts) + 1):
            found.add(".".join(parts[:cut]))
    return found


def _pyc_bytes(source: Path, module_name: str) -> bytes:
    """The `.pyc` body of one module, compiled for the running interpreter."""

    with tempfile.TemporaryDirectory(prefix="pyscriptc-pyc-") as tmp:
        target = Path(tmp) / "module.pyc"
        py_compile.compile(
            str(source),
            cfile=str(target),
            dfile=f"{module_name}.py",
            doraise=True,
            optimize=-1,
        )
        return target.read_bytes()


def write_zip(sources: list[Path], stdlib_dir: Path, zip_path: Path) -> Path:
    """Write a deflated zip of `.pyc` bodies for the kept modules.

    Archive names are the module paths relative to the stdlib root, so
    `python314.zip` answers `import encodings` exactly as the unpacked tree
    did. `.py` files are kept out on purpose: startup must not compile.
    """
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(sources):
            relative = source.relative_to(stdlib_dir)
            module_name = str(relative.with_suffix("")).replace(os.sep, ".")
            archive.writestr(str(relative.with_suffix(".pyc")), _pyc_bytes(source, module_name))
    return zip_path


def stdlib_sources(stdlib_dir: Path, mode: str) -> list[Path]:
    """The kept module sources of a trim mode (`safe` or `minimal`)."""
    if mode == "safe":
        return safe_trim_modules(stdlib_dir)
    if mode == "minimal":
        return minimal_modules(stdlib_dir)
    raise StdlibError(f"--embed-stdlib must be one of {'|'.join(STDLIB_MODES)}, got {mode!r}")


def stdlib_zip_name(version: str) -> str:
    """The zip file name a `pythonXY` landmark expects (`python314.zip`)."""

    return f"python{version.replace('.', '')}.zip"


def pack_stdlib_zip(pack_directory: Path, version: str) -> Path | None:
    """The stdlib zip a pack carries, when its stdlib tree is already zipped.

    A bundle that is itself a compiler (the self-compiled pyscriptc) carries
    the runtime as a zip, not as an unpacked tree: the tree it hands to a
    user program is the zip it already has.
    """

    candidate = pack_directory / "lib" / stdlib_zip_name(version)
    return candidate if candidate.is_file() else None


def carried_modules(pack_directory: Path, version: str, mode: str = DEFAULT_MODE) -> set[str]:
    """The stdlib module names a pack carries, from its tree or its zip.

    The manifest check (PS4301) asks what the trim keeps. A pack with an
    unpacked tree answers from the trim; a pack that carries the zipped
    stdlib answers from the zip.
    """

    tree = pack_directory / "lib" / f"python{version}"
    if tree.is_dir() and any(tree.glob("*.py")):
        return kept_module_names(stdlib_sources(tree, mode))
    zip_path = pack_stdlib_zip(pack_directory, version)
    if zip_path is not None:
        return zip_modules(zip_path)
    return set()


def stage_stdlib(lib_dir: Path, pack_directory: Path, version: str, mode: str = DEFAULT_MODE) -> Path:
    """Replace the unpacked stdlib tree of a staged bundle with its zip.

    Writes `lib/python314.zip` of `.pyc` bodies plus an empty-or-real
    `lib/python3.14/lib-dynload/` (the getpath landmark) and removes the
    unpacked tree, so `sys.path` resolves the zip. Returns the zip path.
    """
    if mode not in STDLIB_MODES:
        raise StdlibError(f"--embed-stdlib must be one of {'|'.join(STDLIB_MODES)}, got {mode!r}")
    stdlib_dir = lib_dir / f"python{version}"
    pack_stdlib = pack_directory / "lib" / f"python{version}"
    if not stdlib_dir.is_dir() and not pack_stdlib.is_dir():
        raise StdlibError(f"the runtime pack has no stdlib tree at {pack_stdlib}")
    zip_path = lib_dir / stdlib_zip_name(version)
    if pack_stdlib.is_dir() and not any(pack_stdlib.glob("*.py")):
        # A pack that carries the zipped stdlib (the self-compiled compiler's
        # own runtime) has no sources to re-trim: the zip travels as is.
        carried = pack_stdlib_zip(pack_directory, version)
        if carried is not None:
            shutil.copy2(carried, zip_path)
            if stdlib_dir.is_dir():
                shutil.rmtree(stdlib_dir)
            dynload = stdlib_dir / "lib-dynload"
            dynload.mkdir(parents=True, exist_ok=True)
            return zip_path
    sources = stdlib_sources(pack_stdlib if pack_stdlib.is_dir() else stdlib_dir, mode)
    write_zip(sources, pack_stdlib if pack_stdlib.is_dir() else stdlib_dir, zip_path)

    if stdlib_dir.is_dir():
        shutil.rmtree(stdlib_dir)
    dynload = stdlib_dir / "lib-dynload"
    dynload.mkdir(parents=True, exist_ok=True)
    return zip_path


def zip_modules(zip_path: Path) -> set[str]:
    """The module names a stdlib zip carries, dotted, submodules included."""

    found: set[str] = set()
    with zipfile.ZipFile(zip_path) as archive:
        for name in archive.namelist():
            found |= _names_of_entry(name)
    return found


def _names_of_entry(name: str) -> set[str]:
    """The dotted module names one zip entry provides (module plus parents)."""

    if not name.endswith(".pyc"):
        return set()
    relative = name[: -len(".pyc")]
    if relative.endswith("/__init__"):
        relative = relative[: -len("/__init__")]
    if not relative:
        return set()
    parts = str(relative).split("/")
    return {".".join(parts[: cut + 1]) for cut in range(len(parts))}


def _tree_module_names(stdlib_dir: Path) -> set[str]:
    """The dotted module names a stdlib tree has a `.py` source for."""

    names: set[str] = set()
    if not stdlib_dir.is_dir():
        return names
    for path in stdlib_dir.rglob("*.py"):
        relative = path.relative_to(stdlib_dir)
        parts = list(relative.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        for cut in range(1, len(parts) + 1):
            names.add(".".join(parts[:cut]))
    return names


def _extension_stems(dynload: Path) -> set[str]:
    """The module names the extension modules of a `lib-dynload/` directory carry."""

    stems: set[str] = set()
    if not dynload.is_dir():
        return stems
    for entry in dynload.iterdir():
        # `_dbm.cpython-314-x86_64-linux-gnu.so` -> `_dbm`
        stem = entry.name.split(".")[0]
        if stem:
            stems.add(stem)
    return stems


def interpreter_modules(pack_directory: Path, version: str) -> set[str]:
    """The stdlib names the bundle's runtime serves without a zip entry.

    A builtin lives inside the runtime binary (`math`, `binascii`, `zlib`,
    `errno`, `fcntl`) and a frozen module inside it (`zipimport`, the
    importlib bootstrap); neither has a `.py` file in the pack's stdlib tree,
    so the trim keeps no source and the zip carries no entry, yet the bundle
    still imports the module from the runtime. Builds disagree on where they
    drew the builtin/extension line, so the set is read off the pack tree
    the bundle actually links instead of the compiler host's own build.

    The pack's extension modules are the exception: they live in
    `lib-dynload/` as `.so` files, and the staged bundle ships an empty
    `lib-dynload/`, so the bundle cannot import them. They stay out of this
    set, and a program that imports one fails the compile (PS4301) instead
    of the run.

    A stdlib name no build of this platform provides (`msvcrt`, `winreg` on
    POSIX) has no tree entry either; `_stdlib_imports` drops such a name
    before the check, and it stays out of the demand here.

    A pack that carries its stdlib already zipped (the self-compiled
    compiler's own runtime) has no tree to read; the zip names the modules
    it carries, and what the runtime serves beyond the zip is the same set
    the runtime binary holds.
    """
    stdlib_dir = pack_directory / "lib" / f"python{version}"
    with_source = _tree_module_names(stdlib_dir)
    if not with_source:
        zipped = pack_stdlib_zip(pack_directory, version)
        if zipped is not None:
            with_source = zip_modules(zipped)
    extensions = _extension_stems(stdlib_dir / "lib-dynload")
    return (set(sys.stdlib_module_names) - with_source) - extensions


def host_modules() -> set[str]:
    """The stdlib names the running interpreter serves without a `.py` file.

    The host link keeps the whole interpreter, so every module the host can
    import counts as carried: a builtin (`math`), a frozen one (`zipimport`)
    and an extension module of the host's `lib-dynload/` alike. The answer
    comes from the host's own module table, not from the way this host was
    built, so it does not change from one machine to the next.
    """

    names = set(sys.builtin_module_names)
    for name in sys.stdlib_module_names:
        try:
            spec = importlib.util.find_spec(name)
        except Exception:
            # A name this host cannot even look up (a broken meta path) is
            # not a module it serves; leave it out.
            continue
        if spec is None:
            continue
        if spec.origin in ("built-in", "frozen"):
            names.add(name)
        elif isinstance(spec.loader, importlib.machinery.ExtensionFileLoader):
            names.add(name)
    return names


def manifest_check(
    carried: set[str],
    imported: list[str],
    mode: str = DEFAULT_MODE,
    interpreter: set[str] | None = None,
) -> None:
    """Fail closed (PS4301) when an imported stdlib module is not carried.

    `carried` is what the trim keeps (`zip_modules` of the zip, or the kept
    module names before it is written); `imported` are the absolute stdlib
    import names the program (and the island bootstrap) uses. A name is
    importable when the zip carries it or a parent of it; the caller passes
    stdlib names only, so relative and third-party imports are not this
    check's business.

    A module the runtime serves without a zip entry (a builtin like `math`
    or `binascii`, a frozen one like `zipimport`) needs no `.py` source in
    the zip, so it counts as carried: the trim drops the source of such a
    module, and the bundle still imports it.

    `interpreter` is that source-free set (see `interpreter_modules`); it
    defaults to the running interpreter's own (see `host_modules`), which is
    the right answer for a host link and for the unit tests, and a bundle
    compile passes the pack's set so the verdict follows the runtime it
    links.
    """

    if interpreter is None:
        interpreter = host_modules()
    missing: list[str] = []
    for name in imported:
        if not name or name in carried or name in interpreter:
            continue
        parts = name.split(".")
        if not any(".".join(parts[: cut + 1]) in carried for cut in range(len(parts))):
            missing.append(name)
    if missing:
        raise StdlibError(
            f"PS4301: the embedded stdlib (mode {mode}) has no module {', '.join(sorted(set(missing)))}; "
            "the trim must keep what the program imports"
        )
