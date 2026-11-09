"""The boxed per-definition fallback planner (issue #612, island floor Stages 2 and 3).

When a function definition hits a fence, this module decides which execution
plane the definition runs on: native (lowered to LLVM as today) or island
(exec'd inside the embedded CPython interpreter, called through
`ps_island_fn_invoke`). The fallback unit is the definition (plan Decision A):
a definition with zero fences lowers native, unchanged. The module top level
is all-or-nothing too (Decision A, Stage 3): when any module-level statement
fences the whole top level runs island-side as the island seed's body, and
when none fences it lowers native as today.

The planner reads the fence list the lowering pass already produced
(`Fence.function` names the enclosing definition, plan Decision A), so it never
lowers anything itself. Everything in this module is boxed-only code (the
ledger counts `src/pyscriptc/boxed.py` whole); branches in shared compiler
files carry `# boxed:begin` / `# boxed:end` markers.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pyscriptc.emit._ir import _flag_of, _has, _node_name, _opt_record, _str_map

# The execution planes of the plane map (plan Decision D).
NATIVE = "native"
ISLAND = "island"

# The entry shim `ps_island_fn_invoke` reads: the dotted name the embedded
# interpreter calls into the module namespace. One island namespace per
# compile, seeded once at init; `fn` is the plain function name inside it.
INVOKE_SYMBOL = "ps_island_fn_invoke"

# The class-side handle: a new reference to the object the island namespace
# holds under one name (issue #628). An island class is callable, so a call
# site reuses `ps_island_fn_invoke`; a class-level attribute read or a class
# taken as a value needs the object first, which this trampoline returns.
DEF_HANDLE_SYMBOL = "ps_island_def_handle"
MOD_DEF_HANDLE_SYMBOL = "ps_island_mod_def_handle"


def _annotation_is_mutable(node: ast.expr | None) -> bool:
    """Whether one parameter annotation is a mutable container (issue #674):
    `list[...]`, `dict[...]` (or bare `dict`), `set[...]` or `bytearray`.
    Scalars, `str` and `bytes` are immutable and stay on the copy-in seam.
    """
    if isinstance(node, ast.Name):
        return node.id in {"list", "dict", "set", "bytearray"}
    if isinstance(node, ast.Subscript):
        value = node.value
        return isinstance(value, ast.Name) and value.id in {"list", "dict", "set", "bytearray"}
    return False


def plane_map(
    fences: list[dict[str, Any]],
    functions: list[dict[str, Any]],
    entry: str | None,
    top_level_island: bool = False,
    demoted_by: dict[str, str] | None = None,
    tree: ast.AST | None = None,
    classes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The per-definition plane map from the lowered fence list.

    `fences` are the module's `as_dict()` fences; `functions` the lowered
    records (`name`, `span`); `classes` the class records (`name`, and
    `island` when the lowerer already decided, issue #628). A definition with
    any fence inside it plans island; a definition with none plans native.
    With `top_level_island` (issue #613, Decision A) the whole module body
    runs island-side: the embedded interpreter executes the module body as
    `__main__` instead of the native entry lowering it, the entry's own
    fences dropped with the top level, and every definition rides the seed.

    Returns the `fallback` summary `debug.py` embeds and the receipt records:
    `{"moduleTopLevel": "native"|"island", "functions": {name: plane, ...},
    "classes": {name: plane, ...}}`.
    """

    fenced: set[str] = set()
    top_import_fences = 0
    top_fence_message = ""
    for fence in fences:
        owner = fence.get("function") or fence.get("definition")
        if owner is not None:
            fenced.add(owner)
        elif fence.get("code") in ("PS2001", "PS2002"):
            top_import_fences += 1
            if not top_fence_message:
                top_fence_message = str(fence.get("message") or fence.get("code") or "")

    if not top_level_island and top_import_fences > 0:
        top_level_island = True
    planes = {}
    for record in functions:
        name = _node_name(record) if isinstance(record, dict) else str(record)
        if name == entry or (name.startswith("__") and name.endswith("__")):
            continue
        planes[name] = ISLAND if name in fenced or top_level_island else NATIVE

    # A class is island when its own definition fences (a decorator, class
    # keywords, a class-body statement, issue #628) or when a fence inside
    # one of its methods names it (`Class.method`). The lowerer passes the
    # decision it made for fences the collect pass never sees, so what
    # `assess` predicts and what the binary does stay one structure.
    class_planes: dict[str, str] = {}
    island_classes: set[str] = set()
    for record in classes or []:
        name = _node_name(record) if isinstance(record, dict) else str(record)
        if isinstance(record, dict) and _has(record, "island"):
            decided = _flag_of(record, "island")
        else:
            decided = None
        if decided is not None:
            is_island = bool(decided)
        else:
            is_island = top_level_island or name in fenced or any(owner.startswith(f"{name}.") for owner in fenced)
        class_planes[name] = ISLAND if is_island else NATIVE
        if is_island:
            island_classes.add(name)

    demotions: dict[str, str] = dict(demoted_by or {})
    if top_level_island:
        # Issue #678: an island top level demotes every definition that
        # would otherwise lower native (there is no island-to-native call
        # seam for the seed body). Record the cause per definition so
        # `assess` and the plane map name the top-level fence - usually an
        # untyped import - that collapsed the module's native share.
        reason = (
            f"module top level is island ({top_fence_message})" if top_fence_message else "module top level is island"
        )
        for name in planes:
            if name not in fenced:
                demotions.setdefault(name, reason)
    fn_nodes: dict[str, Any] = {}
    if tree is not None:
        fn_nodes = {
            node.name: node
            for node in getattr(tree, "body", [])
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
    if island_classes and not top_level_island:
        # Issue #628: cascade the island class to the functions that use it
        # (the #620 rule, one step out). The class object and its instances
        # exist only island-side, so a compiled function that names it joins
        # the island instead of crossing the seam through opaque handles.
        for name, node in fn_nodes.items():
            if planes.get(name) != NATIVE:
                continue
            used = sorted(
                child.id for child in ast.walk(node) if isinstance(child, ast.Name) and child.id in island_classes
            )
            if used:
                planes[name] = ISLAND
                demotions.setdefault(name, f"island class {used[0]}")
    if tree is not None and not top_level_island:
        changed = True
        while changed:
            changed = False
            for name, node in fn_nodes.items():
                if planes.get(name) != ISLAND:
                    continue
                for child in ast.walk(node):
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                        callee = child.func.id
                        if callee in fn_nodes and planes.get(callee) == NATIVE:
                            planes[callee] = ISLAND
                            if callee not in demotions:
                                demotions[callee] = name
                            changed = True

        # Issue #674: the same demotion as the lowerer's `_boxed_resolve`:
        # a native caller of an island callee with a mutable container
        # parameter joins the island, so the mutation is visible to the
        # caller. The annotation on the parameter is the fact here.
        if tree is not None and not top_level_island:
            changed = True
            while changed:
                changed = False
                for name, node in fn_nodes.items():
                    if planes.get(name) != NATIVE:
                        continue
                    for child in ast.walk(node):
                        if not (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)):
                            continue
                        callee_node = fn_nodes.get(child.func.id)
                        if callee_node is None or planes.get(callee_node.name) != ISLAND:
                            continue
                        if any(_annotation_is_mutable(arg.annotation) for arg in callee_node.args.args):
                            planes[name] = ISLAND
                            demotions.setdefault(
                                name,
                                f"island function {callee_node.name} takes a mutable container argument",
                            )
                            changed = True
                            break

    return {
        "moduleTopLevel": ISLAND if top_level_island else NATIVE,
        "functions": planes,
        "classes": class_planes,
        "topLevelFences": 1 if top_level_island else 0,
        "demotedBy": demotions,
    }


def island_handle_type(name: str) -> dict[str, Any]:
    """The opaque handle type of an island value (issues #553, #628)."""

    return {"kind": "class", "name": name, "handle": True, "island": True}


def island_class_type(name: str) -> dict[str, Any]:
    """The handle type of an island class's class object and its instances.

    One type for both sides of the seam (issue #628): the class taken as a
    value and the instance a call returns are the same opaque handle, so an
    annotated local, a parameter and a call site agree.
    """

    return island_handle_type(f"class {name}")


def island_names(planes: dict[str, Any]) -> list[str]:
    """The definitions the plane map routes to the island, in map order."""

    return [name for name, plane in _str_map(planes, "functions").items() if plane == ISLAND]


# The fallback tier of the running compile, module-level like the island
# lane in `stdlib_queue.py`: the lowerer reads it per definition, the CLI
# installs it around the same lowering pass.
_DEFINITIONS_TIER = False


def definitions_tier() -> bool:
    """True when this compile runs under `--fallback=cpython`."""

    return _DEFINITIONS_TIER


@contextmanager
def boxed_definitions(on: bool) -> Iterator[None]:
    """Install the per-definition fallback tier for one lowering pass."""

    global _DEFINITIONS_TIER
    saved = _DEFINITIONS_TIER
    _DEFINITIONS_TIER = on
    try:
        yield
    finally:
        _DEFINITIONS_TIER = saved


def definition_spans(path: Path | str, text: str | None = None) -> list[tuple[str, int, int]]:
    """Return [(name, start_line, end_line), ...] for all definitions in the file."""
    if text is None:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            return []
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    defs: list[tuple[str, int, int]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                start = child.lineno
                end = getattr(child, "end_lineno", child.lineno) or child.lineno
                defs.append((name, start, end))
                walk(child, f"{name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return defs


def find_enclosing_definition(defs: list[tuple[str, int, int]], line: int) -> str | None:
    """The innermost definition enclosing `line`, or None if at module top level."""
    best: tuple[str, int, int] | None = None
    for item in defs:
        name, start, end = item
        if start <= line <= end:
            if best is None or start >= best[1]:
                best = item
    return best[0] if best else None


def is_island_diagnostic(
    err: dict[str, Any],
    source_path: Path,
    plane_map: dict[str, Any],
    tmp_dir: Path | None = None,
) -> bool:
    """True when `err` is located inside an island definition or island top level."""
    err_path_str = str(err.get("path", ""))
    target_file: Path | None = None
    if source_path.is_file():
        target_file = source_path
    elif err_path_str:
        candidate = Path(err_path_str)
        if not candidate.is_absolute():
            # The checker prints paths relative to the compiler's working
            # directory (it runs with that CWD); the temp dir anchor is the
            # fallback for a path it relativized against its own scratch
            # config. A `..` chain past the root resolves against the CWD
            # and lands on the real file (found on yt_dlp: every one of
            # 8,379 diagnostics in a `--program` compile of a package kept
            # the whole preflight failing because the anchor was wrong).
            anchors = [Path.cwd(), *([tmp_dir] if tmp_dir is not None else [])]
            resolved = None
            for anchor in anchors:
                found = (anchor / candidate).resolve()
                if found.is_file():
                    resolved = found
                    break
            if resolved is None:
                resolved = candidate.resolve()
            candidate = resolved
        else:
            candidate = candidate.resolve()
        if candidate.is_file():
            target_file = candidate

    if target_file is None and source_path.is_file():
        target_file = source_path
    if target_file is None:
        return False

    line = int(err.get("line", 0) or 0)
    if line <= 0:
        return False

    top_level_island = plane_map.get("moduleTopLevel") == ISLAND
    defs = definition_spans(target_file)
    enclosing = find_enclosing_definition(defs, line)

    if enclosing is None:
        return top_level_island

    fns = _str_map(plane_map, "functions")
    cls = _str_map(plane_map, "classes")
    if enclosing in fns:
        return fns[enclosing] == ISLAND
    if enclosing in cls:
        return cls[enclosing] == ISLAND

    parts = enclosing.split(".")
    for i in range(len(parts), 0, -1):
        prefix = ".".join(parts[:i])
        if prefix in fns:
            return fns[prefix] == ISLAND
        if prefix in cls:
            return cls[prefix] == ISLAND

    return top_level_island


# The program-wide island (Refs #641): when the fallback tier is on and any
# unit of a multi-module program needs the island, the whole program runs
# island-side. The seed is a bootstrap that registers every unit's source
# in an in-memory finder (so `import`, relative imports, cycles, `__name__`,
# `__file__` and import order are CPython's own) and execs the entry module
# as `__main__`. The per-unit plans stop at the module boundary, so a
# cross-module call between a native and an island unit has no seam to
# cross; the coarse unit is the program.
# Issue #665: the finder hands out the compile-time paths as module
# origins, so `Path(__file__).parent / "data.json"` in a relocated bundle
# reads a tree that is gone. The compiler stages the program directory's
# non-Python files beside the binary (`<binary dir>/pyscriptc-data/<program
# name>/`) and passes the pieces here, so every origin, the entry `__file__`
# and `sys.path[0]` are rebased onto the staged tree, resolved at run time
# from the running image (`sys.executable`), so a relocated output
# directory keeps working.
# Issue #782: the compiler may also embed the entry's compiled code object
# (`__pyscriptc_seedcache__.codes`, a zlib-compressed marshalled `{name:
# code}` mapping built for the exact interpreter the binary links). The
# loader validates the header (the running interpreter's magic number and
# a matching source hash); a missing, stale or invalid entry falls back to
# compiling the embedded source, so a foreign-magic binary never breaks -
# it starts slower. Issue #712: the other units ship as marshalled code
# slices in `__pyscriptc_seed__`'s index and body blobs - no source text.
_PROGRAM_BOOTSTRAP = """\
import os as _os, sys as _sys, importlib.abc as _abc, importlib.util as _iu, marshal as _ma, zlib as _zl, hashlib as _ha
_PS_SEED = _sys.modules.get('__pyscriptc_seed__')
_PS_INDEX = _ma.loads(_PS_SEED.index) if _PS_SEED is not None else {}
_PS_BODY = _PS_SEED.body if _PS_SEED is not None else b''
_PS_DATA_DIR = %(data_dir)s
_PS_PROG_NAME = %(prog_name)s
_PS_BASE = %(base)s
_PS_SELFPACK = %(selfpack)s
_PS_SELFPACK_DIR = %(selfpack_dir)s


def _ps_staged():
    if _PS_DATA_DIR is None:
        return None
    exe = getattr(_sys, "executable", "") or ""
    if not exe:
        return None
    root = _os.path.dirname(_os.path.abspath(exe))
    return _os.path.join(root, _PS_DATA_DIR, _PS_PROG_NAME)


# The selfpack (issue #678 follow-up): the compiled compiler carries its own
# package data (the runtime C sources, target.json, native_packages.json,
# the vendored typeshed) as one compressed payload. The first compiler
# command materializes it under one cache directory and rewrites the module
# origins of the embedded finder onto the copy, so every
# `Path(__file__).with_name(...)` and `runtime_dir()` lookup resolves there.
def _ps_selfpack():
    if not _PS_SELFPACK:
        return None
    from io import BytesIO as _io_BytesIO
    import zipfile as _zipfile
    import base64 as _base64
    import shutil as _shutil
    import hashlib as _hashlib
    override = _os.environ.get("PYSC_SELFPACK_DIR")

    raw = _base64.b64decode(_PS_SELFPACK)
    digest = _hashlib.sha256(raw).hexdigest()[:16]
    if override:
        base = _os.path.join(override, "pyscriptc-selfpack")
    else:
        cache = _os.environ.get("PYSC_CACHE_DIR")
        if not cache:
            cache = _os.path.join(_os.path.expanduser("~"), ".cache", "pyscriptc")
        base = _os.path.join(cache, "selfpack")
    target = _os.path.join(base, digest)
    marker = _os.path.join(target, ".selfpack-complete")
    if not _os.path.isfile(marker):
        scratch = _os.path.join(base, "." + digest + ".extracting")
        _shutil.rmtree(scratch, ignore_errors=True)
        _os.makedirs(scratch, exist_ok=True)
        with _zipfile.ZipFile(_io_BytesIO(raw)) as _archive:
            _archive.extractall(scratch)
        with open(_os.path.join(scratch, ".selfpack-complete"), "w"):
            pass
        _shutil.rmtree(target, ignore_errors=True)
        _os.makedirs(base, exist_ok=True)
        _os.rename(scratch, target)
    return target


_PS_SELFPACK_ROOT = None
try:
    _PS_SELFPACK_ROOT = _ps_selfpack()
except Exception:
    _PS_SELFPACK_ROOT = None


def _ps_rebase(path):
    staged = _ps_staged()
    if staged is not None:
        if path == _PS_BASE:
            return staged
        if path.startswith(_PS_BASE + _os.sep):
            return _os.path.join(staged, path[len(_PS_BASE) + 1 :])
        parent = _os.path.dirname(_PS_BASE)
        if path == parent:
            return _os.path.dirname(staged)
    if _PS_SELFPACK_ROOT:
        # The selfpack active: every package origin resolves into the
        # extracted tree. The module bodies still come from _PS_INDEX (the
        # loader serves them from memory); only the Path(__file__) the
        # compiled code reads back resolves there, so runtime_dir(),
        # target.json and the typeshed lookups land on the extracted copy.
        if path.startswith(_PS_BASE + _os.sep):
            return _os.path.join(_PS_SELFPACK_ROOT, path[len(_PS_BASE) + 1 :])
        if path == _PS_BASE:
            return _PS_SELFPACK_ROOT
    return path


# Issue #782: the precompiled code cache the runtime registered before the
# seed ran (or None when this binary carries none). One decompress, then
# one unmarshal per module, on the module's first import.
_PS_CODES = None
try:
    import __pyscriptc_seedcache__ as _sc

    _raw = getattr(_sc, "codes", None)
    if _raw is not None:
        _PS_CODES = _ma.loads(_zl.decompress(_raw))
except Exception:
    _PS_CODES = None


def _ps_code(name, path, source):
    # The module's code object: from the embedded cache when the entry is
    # present, was built for this interpreter (the `.pyc` magic number) and
    # was built from this exact source (sha256); the source compile is the
    # fallback, never trusted blindly from a stale cache.
    entry = _PS_CODES.get(name) if _PS_CODES else None
    if entry is not None:
        try:
            magic, digest, body = entry
            if magic == _iu.MAGIC_NUMBER and digest == _ha.sha256(source.encode("utf-8")).digest():
                return _ma.loads(body)
        except Exception:
            pass
    return compile(source, path, "exec")


def _ps_seed_code(name):
    # The module body is marshalled bytecode, deflated on its own: the body
    # blob stays in the binary's read-only pages and only the modules a run
    # imports are inflated and unmarshalled.
    _origin, _is_pkg, _offset, _length = _PS_INDEX[name]
    _raw = bytes(_PS_BODY[_offset:_offset + _length])
    return _ma.loads(_zl.decompress(_raw))


class _PsLoader(_abc.Loader):
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        exec(_ps_seed_code(module.__name__), module.__dict__)
class _PsFinder(_abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        found = _PS_INDEX.get(name)
        if found is None:
            return None
        origin = _ps_rebase(found[0])
        spec = _iu.spec_from_loader(name, _PsLoader(), origin=origin, is_package=found[1])
        spec.has_location = True
        if found[1]:
            spec.submodule_search_locations = [_os.path.dirname(origin)]
        return spec
_sys.meta_path.insert(0, _PsFinder())
_sys.path.insert(0, _ps_rebase(%(path0)s))
__file__ = _ps_rebase(%(main_path)s)
__package__ = %(package)s
if %(package_mode)s:
    # The `python -m pkg` shape (issue #765): CPython records the entry
    # module's own path as argv[0], so argparse's default `prog` is
    # `__main__.py` and the usage line names the script, not the binary.
    # `__file__` is that path, rebased onto the staged tree when the
    # program staged data.
    if _sys.argv:
        _sys.argv[0] = __file__
    else:
        _sys.argv = [__file__]
exec(_ps_code(%(main_name)s, __file__, %(main_source)s), globals())
"""


def _module_code_entry(source: str, path: str) -> tuple[bytes, bytes, bytes] | None:
    """One cache body for a module: `(magic number, source digest, marshalled code)`.

    The marshalled code object is pinned to the compiling interpreter's
    `.pyc` magic number; the entry carries the source's sha256 so a stale
    cache (source changed after the cache was built) is refused, and the
    loader re-checks the magic at run time against the interpreter that
    actually runs the binary. None when the module does not compile.
    """
    import hashlib
    import marshal
    from importlib.util import MAGIC_NUMBER

    try:
        code = compile(source, path, "exec", dont_inherit=True)
    except ValueError:
        return None
    except SyntaxError:
        return None
    return (
        MAGIC_NUMBER,
        hashlib.sha256(source.encode("utf-8")).digest(),
        marshal.dumps(code),
    )


def code_cache_blob(entries: dict[str, tuple[bytes, bytes, bytes]]) -> bytes:
    """The zlib-compressed marshalled code cache of a program's units.

    `entries` maps each module name to its `_module_code_entry` triple (the
    magic number of the compiling interpreter and the marshalled code
    object, with the source digest inside). The outer marshal keeps the
    insertion order, so the same inputs give the same bytes; the caller
    sorts before calling when it wants a canonical order. Empty input
    returns an empty blob, which the run-time loader treats as no cache.
    """
    import marshal
    import zlib

    return zlib.compress(marshal.dumps(entries), 9)


# The island group's bootstrap (the plane split's cycle rule): one exec
# under CPython that installs a finder over the group's own module sources
# and imports every group module, so the cycle resolves the way CPython
# always resolves it. The group is closed under imports, so no group module
# reaches for a native definition of another module; the native lane reaches
# the group through the boxed seam, which reads `sys.modules` under the
# group's real names.
_ISLAND_GROUP_BOOTSTRAP = """\
import sys as _sys, importlib.abc as _abc, importlib.util as _iu
_PS_GROUP = %(modules)s


class _PsGroupLoader(_abc.Loader):
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        path, _is_pkg, source = _PS_GROUP[module.__name__]
        exec(compile(source, path, "exec"), module.__dict__)


class _PsGroupFinder(_abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        found = _PS_GROUP.get(name)
        if found is None:
            return None
        origin = found[0]
        spec = _iu.spec_from_loader(name, _PsGroupLoader(), origin=origin, is_package=found[1])
        spec.has_location = True
        if found[1]:
            import os as _os
            spec.submodule_search_locations = [_os.path.dirname(origin)]
        return spec


_sys.meta_path.insert(0, _PsGroupFinder())
for _name in %(order)s:
    __import__(_name)
"""


def island_group_bootstrap(
    sources: dict[str, str],
    packages: dict[str, bool],
    paths: dict[str, str],
) -> str:
    """The island group's bootstrap source: the finder plus the import order.

    `sources` maps each group module's real name to its source text,
    `packages` to whether it is a package unit, `paths` to its compile-time
    path (the origin the finder hands out). The import order is the group's
    finish order (a module after the modules it imports), so a module body
    that reads a sibling's binding finds it; a cycle member's body may read a
    partially initialized sibling, exactly as under CPython.
    """

    modules = {name: (paths[name], packages[name], sources[name]) for name in sources}
    order = sorted(sources, key=lambda n: (n.count("."), n))
    return _ISLAND_GROUP_BOOTSTRAP % {"modules": repr(modules), "order": repr(order)}


def program_island_source(
    units: list[tuple[str, Path, bool]],
    *,
    entry: str,
    main_package: str,
    root: Path,
    path0: Path,
    staged_data: tuple[str, str] | None = None,
    selfpack: str = "",
    selfpack_dir: str = "",
    package_mode: bool = False,
) -> tuple[str, tuple[bytes, bytes], bytes | None]:
    """The island seed of a whole program: finder bootstrap plus the entry.

    Returns the `(bootstrap source, (index blob, body blob), code cache)`
    triple the binary carries. The bootstrap is a small finder program; the
    module bodies live in the two blobs as marshalled bytecode (issue #712),
    so the binary does not compile a 10 MB source blob at startup and does
    not hold the program text in memory.

    The index blob is the marshalled `{name: (origin, is_package, offset,
    length)}` table; the body blob is the concatenation of the per-module
    deflated code objects the index points into. A run inflates and
    unmarshals only the modules it imports.

    `staged_data` is the `(data directory name, program name)` pair of the
    staged data tree (issue #665), or None when the program staged nothing.
    The bootstrap resolves it against the running image, so a relocated
    output directory still finds its data files.

    `selfpack` is the base64 payload of the compiled package's own data
    files (issue #678 follow-up): the compiler embedding itself carries
    its runtime sources and tables so the binary is self-contained. The
    bootstrap extracts it once under the digest directory and rebases the
    data-file origins onto the copy.

    `code cache` (issue #782) is the zlib-compressed marshalled code cache
    of the entry unit (None when it did not compile, in which case the
    bootstrap compiles the entry's source at start exactly as before the
    cache existed); the other units need no cache, their bodies are already
    marshalled code in the body blob.
    """

    import marshal
    import zlib

    index: dict[str, tuple[str, bool, int, int]] = {}
    body = bytearray()
    main_path = ""
    main_source = ""
    for name, path, is_package in units:
        if name == entry:
            main_path, main_source = str(path), path.read_text(encoding="utf-8")
            continue
        text = path.read_text(encoding="utf-8")
        # The optimizer level is pinned to 0 so the bytecode does not depend
        # on how the compiler itself was invoked (`python -O`).
        code = compile(text, str(path), "exec", dont_inherit=True, optimize=0)
        blob = zlib.compress(marshal.dumps(code), 9)
        index[name] = (str(path), is_package, len(body), len(blob))
        body.extend(blob)
    source = _PROGRAM_BOOTSTRAP % {
        "data_dir": repr(staged_data[0] if staged_data is not None else None),
        "prog_name": repr(staged_data[1] if staged_data is not None else None),
        "base": repr(str(root.resolve())),
        "path0": repr(str(path0)),
        "main_path": repr(main_path),
        "package": repr(main_package),
        "package_mode": repr(package_mode),
        "main_source": repr(main_source),
        "selfpack": repr(selfpack),
        "selfpack_dir": repr(selfpack_dir),
        "main_name": repr(entry),
    }
    # The code cache covers the entry only: every other unit already ships
    # as a marshalled code slice in the body blob, so a second marshalled
    # copy under the source-digest guard would be dead bytes.
    main_built = _module_code_entry(main_source, main_path)
    cache = code_cache_blob({entry: main_built}) if main_built is not None else None
    return source, (marshal.dumps(index), bytes(body)), cache


def program_island_module(
    source: str, root: Path, seed: tuple[bytes, bytes], code_cache: bytes | None = None
) -> dict[str, Any]:
    """The merged IR module of a program-wide island: no native code at all.

    `seed` is the `(index blob, body blob)` pair of the bytecode-backed module
    table (issue #712); the IR carries it base64-encoded, the only encoding a
    JSON IR can hold bytes in.

    `code_cache` (issue #782) is the zlib-compressed marshalled code cache
    of the entry unit, or None when the running interpreter produced none;
    the emitter embeds it and registers it before the seed execs, and the
    bootstrap falls back to compiling the entry's source without it.
    """

    from base64 import b64encode

    from pyscriptc.ir import IR_VERSION, SUBSET, cpython_version

    index_blob, body_blob = seed
    module: dict[str, Any] = {
        "irVersion": IR_VERSION,
        "subset": SUBSET,
        "cpython": cpython_version(),
        "sourceFile": str(root),
        "entry": None,
        "functions": [],
        "classes": [],
        "fences": [],
        "fallback": {
            "moduleTopLevel": ISLAND,
            "functions": {},
            "classes": {},
            "topLevelFences": 1,
            "demotedBy": {},
        },
        "islandSource": source,
        "islandSeedIndex": b64encode(index_blob).decode("ascii"),
        "islandSeedBody": b64encode(body_blob).decode("ascii"),
    }
    if code_cache is not None:
        module["islandCodeCache"] = code_cache.hex()
    return module


def unit_needs_island(module: dict[str, Any]) -> bool:
    """True when one unit's own plan put anything on the island."""

    plan = _opt_record(module, "fallback") or {}
    return (
        plan.get("moduleTopLevel") == ISLAND
        or ISLAND in _str_map(plan, "functions").values()
        or ISLAND in _str_map(plan, "classes").values()
        or bool(module.get("islandSource"))
    )
