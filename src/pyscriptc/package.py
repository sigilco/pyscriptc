"""Compile a directory as one module and resolve relative imports.

Each ``.py`` file becomes a dotted module under the directory name.
``from .util import add`` and ``from . import VERSION`` bind a function, a
module-level constant, or a class from another file in that tree. A class
export is the class the defining module already emits, including a subclass
of a class exported by another module in the tree.

Two directory shapes exist:

* A **package** directory: the tree is a library. ``__main__.py`` is the
  ``python -m`` script and is not an importable module, so it is skipped and
  the compile says so.
* A **program** directory: the tree has a ``__main__.py`` at its root. That
  file is the entry point, the sibling modules are imported by their plain
  names (``import util``, ``from pkg import x``) exactly as ``sys.path[0]``
  would resolve them, and the whole tree compiles into one native binary.

A single file still refuses a relative import.
"""

from __future__ import annotations

import ast
import copy
from pathlib import Path
from typing import Any

from pyscriptc import boxed as _boxed
from pyscriptc.builtins import USER_EXC_BASE
from pyscriptc.errors import IRError
from pyscriptc.ir import IR_VERSION, SUBSET, cpython_version
from pyscriptc.lower import Lowerer
from pyscriptc.stdlib_queue import dependency_lanes, ffi_modules, staged_resources

# Issue #665: the staged data root of the running program-island compile.
# `_program_island` writes the program directory's non-Python files here
# (relative to the compile-time directory) and the bootstrap rebases every
# module origin onto it, so `Path(__file__).parent` data lookups survive a
# relocation. Set by `stage_program_data_target`, cleared after the compile.
_STAGED_DATA_TARGET: Path | None = None


def stage_program_data_target() -> Path | None:
    """The staged data root of the running compile, None when none."""

    return _STAGED_DATA_TARGET


def lower_directory(
    root: Path,
    program_mode: bool = False,
    bridge: Any = None,
    entry: str | None = None,
    prefix: str = "",
    ffi: list[Any] | None = None,
    target: str | None = None,
    dep_roots: list[Path] | None = None,
    dep_lane_report: dict[str, dict[str, str]] | None = None,
    resource_root: str = "",
    config: Any = None,
    staged_data: list[tuple[Path, bytes]] | None = None,
) -> tuple[dict[str, Any], str]:
    """Lower a directory tree into one IR module.

    With ``entry`` the caller names the entry module (project mode): the
    module keeps its dotted name and its body becomes the program's entry
    function, exactly as ``__main__.py`` does in a program directory.
    ``prefix`` names the package the tree sits in (project mode's module
    root), so ``src/mylib`` lowers as ``mylib.*`` and not ``src.mylib.*``.

    ``dep_roots`` are extra module roots (one per unpacked pure-Python
    dependency wheel, issue #398): each lowers into the same merged IR with
    its plain import name, exactly the way a program's sibling modules
    merge. ``dep_lane_report`` names the locked dependencies by import
    name, so a dependency on a lane the compiler cannot take is refused
    with the distribution and the lane hint.

    Fences follow the reach rule (issue #424): the program's own units are
    all-or-nothing, and a dependency unit that the program's import graph
    reaches refuses the compile with its fence, its code and the
    distribution that ships it. A dependency unit the program never
    reaches is pruned: CPython runs a module body only at the import that
    reaches it, so a body the program never triggers cannot affect the
    output, and the fences inside the pruned unit are reported in the
    ``_notes`` list instead of blocking the compile.
    """

    if config is None:
        try:
            from pyscriptc.config import load_config

            config = load_config(root)
        except Exception:
            config = None

    units, skipped = _discover(root, program_mode, entry=entry, prefix=prefix)
    program = program_mode or _is_program_dir(root)
    dep_units, dep_fences = _discover_dep_roots(dep_roots or [])
    if dep_fences:
        return _fenced_module(root, dep_fences), ""
    kept, catalogued, pruned = _prune_unreached(units, dep_units, program=program, entry=entry)
    merged, notes = _lower_units(
        [*units, *kept],
        root,
        program=program,
        entry=entry,
        bridge=bridge,
        ffi=ffi,
        target=target,
        dep_names={name for name, _path, _is_package in kept},
        dep_lane_report=dep_lane_report,
        catalog_units=catalogued,
        resource_root=resource_root,
        config=config,
        main_package=(prefix or root.name) if entry is None and program_mode and not _is_program_dir(root) else "",
        path0=root.resolve().parent
        if (program_mode or entry is not None) and not _is_program_dir(root)
        else root.resolve(),
        staged_data=staged_data or [],
    )
    if skipped:
        notes = [*notes, *[_skip_note(root, rel) for rel in skipped]]
    notes = [*notes, *[_pruned_note(name, path) for name, path, _is_package in pruned]]
    notes = [*notes, *[_catalogued_note(name, path) for name, path, _is_package in catalogued]]
    if notes:
        merged["_notes"] = notes
    return merged, ""


def _prune_unreached(
    units: list[tuple[str, Path, bool]],
    dep_units: list[tuple[str, Path, bool]],
    *,
    program: bool,
    entry: str | None,
) -> tuple[list[tuple[str, Path, bool]], list[tuple[str, Path, bool]], list[tuple[str, Path, bool]]]:
    """Split the dependency units by how the program's import graph reaches them.

    Three buckets come out:

    * **kept** - the dependency units the entry's import graph reaches.
      They lower into the program and their fences refuse the compile.
    * **catalogued** - the dependency units the program's tree references
      but the entry does not reach. They stay in the export table (the
      import of an unreached program module binds the way CPython's
      would), but they do not lower and their fences do not refuse.
    * **pruned** - the dependency units nothing in the program imports.
      They are dropped entirely and their fences stop being root fences
      (issue #424).

    The program's own units are always kept: the whole-tree rule stays for
    code the user wrote.

    The pruning is sound because of CPython's import-time side effect
    rule: a module body runs once, at the import that reaches it, and
    never otherwise. A dependency module the entry never reaches never
    runs, so its body cannot affect the program's output and a fence
    inside it cannot change what the compiled program does.

    The reach walk is static and name-based: it follows the same import
    statements the program plan does, and a dynamic import
    (`importlib.import_module`, `__import__`) does not lower, so no
    lowered program can reach past it.
    """

    if not dep_units:
        return [], [], []
    reached = _reachable_modules([*units, *dep_units], _program_seeds(units, program=program, entry=entry))
    referenced = _reachable_modules([*units, *dep_units], [name for name, _path, _is_package in units])
    kept: list[tuple[str, Path, bool]] = []
    catalogued: list[tuple[str, Path, bool]] = []
    pruned: list[tuple[str, Path, bool]] = []
    for unit in dep_units:
        if unit[0] in reached:
            kept.append(unit)
        elif unit[0] in referenced:
            catalogued.append(unit)
        else:
            pruned.append(unit)
    return kept, catalogued, pruned


def _lower_units(
    units: list[tuple[str, Path, bool]],
    root: Path,
    *,
    program: bool,
    entry: str | None,
    bridge: Any,
    ffi: list[Any] | None,
    target: str | None,
    dep_names: set[str],
    dep_lane_report: dict[str, dict[str, str]] | None = None,
    catalog_units: list[tuple[str, Path, bool]] | None = None,
    resource_root: str = "",
    config: Any = None,
    main_package: str = "",
    path0: Path | None = None,
    staged_data: list[tuple[Path, bytes]] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Lower a set of module units into one IR module.

    The units are the program's own modules plus the dependency roots'
    (issue #398), and they share one export table, so a dependency module
    imports by its plain name exactly like a sibling. Any fence in any
    unit that is still here refuses the whole compile: the pruning in
    ``lower_directory`` has already dropped the unreached dependency
    units, so a fence that survives the split is a reached one. A fence
    inside a reached dependency names the distribution that ships it, so
    the user knows which dependency to fix.

    ``catalog_units`` are the dependency units the program references but
    the entry does not reach (issue #424). They contribute their exports
    to the table, so an import in an unreached program module still
    binds, but they lower no code and their fences do not refuse: the
    entry never imports them, so CPython never runs their bodies.
    """

    exports, failed = _catalog([*units, *(catalog_units or [])], program, entry=entry, target=target, config=config)

    # A bridge is built per file: mypy's TypeMap covers one module, so a
    # package tree runs the checker once per unit and lowering consults the
    # map of the file it is in (issue #258). A plain `CheckerBridge` is a
    # single-file map and stays the one-bridge-per-compile shape.
    per_file = getattr(bridge, "for_file", None)
    file_bridges: dict[str, Any] = {}

    def bridge_for(path: Path) -> Any:
        if per_file is None:
            return bridge
        found = file_bridges.get(str(path))
        if found is None:
            found = per_file(path)
            file_bridges[str(path)] = found
        return found

    if program:
        fences, owners = _program_plan(units, exports, entry=entry)
        if fences:
            # boxed:begin
            island = _program_island(units, root, entry, main_package, path0, staged_data=staged_data or [])
            if island is not None:
                return island, []
            # boxed:end
            return _fenced_module(root, fences), []
    else:
        owners = {}

    functions: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    fences: list[dict[str, Any]] = []
    class_names: dict[str, dict[str, Any]] = {}
    exc_n = USER_EXC_BASE
    needs_island = False  # boxed
    with ffi_modules(ffi or []), dependency_lanes(dep_lane_report or {}), staged_resources(resource_root):
        for name, path, is_package in units:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
            if program:
                tree = _inject_inits(tree, owners.get(name, []), exports, name, is_package)
            lowerer = Lowerer(str(path), text, target=target, config=config)
            lowerer.module_name = name
            lowerer.is_package = is_package
            lowerer.package = exports
            lowerer.exc_n = exc_n
            lowerer.program = program
            lowerer.program_roots = dep_names
            lowerer.module_init = program and name != "__main__" and name != entry
            lowerer.entry_module = entry is not None and name == entry
            lowerer.bridge = bridge_for(path)
            module = lowerer.module(tree)
            # boxed:begin
            needs_island = needs_island or _boxed.unit_needs_island(module)
            # Refs #641: the module-level island refusals (a cross-package
            # import of a definition the other module planned for the island)
            # belong to no definition, so the unit's own island planning
            # drops them with the module-level fences. The merge keeps them.
            if lowerer._boxed_module_fences:
                module["fences"] = [*module["fences"], *(f.as_dict() for f in lowerer._boxed_module_fences)]
            # boxed:end
            exc_n = lowerer.exc_n
            for fn in module["functions"]:
                functions.append(fn)
            for cls in module["classes"]:
                previous = class_names.get(cls["name"])
                if previous is not None:
                    if previous["fields"] == cls["fields"] and previous["methods"] == cls["methods"]:
                        continue
                    fences.append(
                        {
                            "code": "PS2002",
                            "message": f"class {cls['name']} is defined in more than one module",
                            "span": cls["span"],
                            "function": None,
                        }
                    )
                    continue
                class_names[cls["name"]] = cls
                classes.append(cls)
            fences.extend(module["fences"])
    # boxed:begin
    if program and (needs_island or fences):
        island = _program_island(units, root, entry, main_package, path0, staged_data=staged_data or [])
        if island is not None:
            return island, []
    # boxed:end
    fences = _promote_definition_fences(fences, failed)
    fences = _mark_dep_fences(fences, units, dep_names, dep_lane_report)
    merged = {
        "irVersion": IR_VERSION,
        "subset": SUBSET,
        "cpython": cpython_version(),
        "sourceFile": str(root),
        "entry": _program_entry(functions, entry) if program else (functions[0]["name"] if functions else None),
        "functions": functions,
        "classes": classes,
        "fences": fences,
    }
    return merged, []


# boxed:begin
def _program_island(
    units: list[tuple[str, Path, bool]],
    root: Path,
    entry: str | None,
    main_package: str,
    path0: Path | None,
    staged_data: list[tuple[Path, bytes]] | None = None,
) -> dict[str, Any] | None:
    """The program-wide island module, or None when the tier is off.

    Under `--fallback=cpython` a multi-module program with any unit on the
    island runs whole island-side (Refs #641): the per-unit plans stop at
    the module boundary, so the coarse unit is the program.

    `staged_data` are the program directory's non-Python files (issue
    #665): they ride the output under the same relative paths, and the
    bootstrap rebases every module origin from the compile-time directory
    onto the staged root, so `Path(__file__).parent` data lookups survive
    a relocation. When nothing is staged the origins stay compile-time
    (divergence 46 pins the unstaged lookup paths).
    """

    if not _boxed.definitions_tier():
        return None
    main = entry if entry is not None else "__main__"
    if not any(name == main for name, _path, _is_package in units):
        return None
    if entry is not None:
        is_package = any(name == entry and pkg for name, _path, pkg in units)
        main_package = entry if is_package else entry.rpartition(".")[0]
    staged = stage_program_data(root, staged_data or [])
    # The selfpack (issue #678 follow-up): a program compile of the pyscriptc
    # package itself embeds the package's data files (runtime C sources,
    # target.json, native_packages.json, the vendored typeshed) so the
    # emitted binary is self-contained. Any other program compiles without
    # one: the payload would be dead weight in the binary.
    selfpack_payload, selfpack_dir = "", ""
    if root.name == "pyscriptc" and (root / "target.json").is_file():
        from pyscriptc import selfpack as _selfpack

        encoded = _selfpack.build_payload(_selfpack.collect_package_data(root))
        selfpack_payload = encoded
        selfpack_dir = _selfpack.digest_of(encoded)
    source = _boxed.program_island_source(
        units,
        entry=main,
        main_package=main_package,
        root=root,
        path0=path0 or root,
        staged_data=staged,
        selfpack=selfpack_payload,
        selfpack_dir=selfpack_dir,
    )
    return _boxed.program_island_module(source, root)


def stage_program_data(
    root: Path,
    staged_data: list[tuple[Path, bytes]],
) -> tuple[str, str] | None:
    """Stage the program directory's non-Python files; returns the staged names.

    Issue #665: under the program-wide island the binary carries the code,
    but a `Path(__file__).parent / "data.json"` lookup resolves against the
    compile-time directory, which is gone on another machine. The staged
    files keep their program-relative paths under
    `<binary dir>/pyscriptc-data/<program name>/`, and the bootstrap rebases
    every module origin onto that tree, so the lookups land on the copy.
    The tree is resolved at run time from the running image, so a relocated
    output directory keeps working. The Python sources are not copied (the
    finder serves the embedded text). Empty input stages nothing, so a
    program with no sibling files keeps today's behavior exactly.
    """

    if not staged_data:
        return None
    root_dir = stage_program_data_target()
    if root_dir is None:
        return None
    for rel, body in sorted(staged_data):
        target = root_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    return (root_dir.parent.name, root_dir.name)


# boxed:end


def _program_entry(functions: list[dict[str, Any]], entry: str | None = None) -> str | None:
    """The program's entry: the entry module's entry function, or the first function.

    The entry module's synthesized entry (`__entry__`, or `main` when the
    module has no `main` of its own) is the program's script body; it wins
    over the module's own functions.
    """

    if entry is not None:
        for suffix in ("__entry__", "main"):
            candidate = f"{entry}.{suffix}"
            if any(fn["name"] == candidate for fn in functions):
                return candidate
        for fn in functions:
            if fn["name"].startswith(entry + "."):
                return fn["name"]
    for fn in functions:
        if fn["name"] in {"__main__.main", "__main__.__entry__"}:
            return fn["name"]
    for fn in functions:
        if fn["name"].startswith("__main__."):
            return fn["name"]
    return functions[0]["name"] if functions else None


def _fenced_module(root: Path, fences: list[dict[str, Any]]) -> dict[str, Any]:
    """A module that carries only fences: the compile stops before emission."""

    return {
        "irVersion": IR_VERSION,
        "subset": SUBSET,
        "cpython": cpython_version(),
        "sourceFile": str(root),
        "entry": None,
        "functions": [],
        "classes": [],
        "fences": fences,
    }


def _program_seeds(units: list[tuple[str, Path, bool]], *, program: bool, entry: str | None) -> list[str]:
    """The modules a reach walk starts from.

    A program starts at its entry module: that is the code CPython runs,
    and what it imports is what runs. A library package has no entry, so
    the whole tree is the seed: every module can be imported by the
    package's consumer.
    """

    names = [name for name, _path, _is_package in units]
    if entry is not None and entry in names:
        return [entry]
    if program:
        mains = [name for name in ("__main__", "main") if name in names]
        if mains:
            return mains
    return names


def _reachable_modules(
    units: list[tuple[str, Path, bool]],
    seeds: list[str],
) -> set[str]:
    """The module names the import graph reaches from the seeds.

    The walk follows the same import statements the program plan does,
    plus the parent edge a submodule import implies (`import pkg.sub`
    imports `pkg` first: CPython runs its body before the submodule
    binds). Only names in the unit table count; every other import is
    stdlib, a pack or a refused name and never a unit.
    """

    trees: dict[str, ast.Module] = {}
    packages: dict[str, bool] = {}
    for name, path, is_package in units:
        trees[name] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        packages[name] = is_package
    # The reach walk needs the module names, not their exports: an empty
    # bucket per module is enough for the import resolvers to resolve a
    # name against the unit table.
    exports: dict[str, dict[str, dict[str, Any]]] = {name: {} for name in trees}

    def parents_of(name: str) -> list[str]:
        """The package bodies an import of `name` runs first.

        `import pkg.sub` imports `pkg` before `pkg.sub`: CPython runs the
        package's body first, so a fence in `pkg/__init__.py` matters even
        when the program only names the submodule.
        """

        parts = name.split(".")
        found: list[str] = []
        for i in range(1, len(parts)):
            parent = ".".join(parts[:i])
            if parent in exports and parent not in found:
                found.append(parent)
        return found

    def edges_of(name: str) -> list[str]:
        found = _program_imports(trees[name], exports, name, packages[name])
        edges: list[str] = []
        for target in found:
            if target not in exports:
                continue
            if target not in edges:
                edges.append(target)
            for parent in parents_of(target):
                if parent not in edges:
                    edges.append(parent)
        return edges

    reached: set[str] = set()
    stack: list[str] = []
    for seed in seeds:
        if seed in exports and seed not in stack:
            stack.append(seed)
        stack.extend(parents_of(seed))
    while stack:
        name = stack.pop()
        if name not in exports or name in reached:
            continue
        reached.add(name)
        stack.extend(edges_of(name))
    return reached


def _pruned_note(name: str, path: Path) -> str:
    return (
        f"note: pruned {name} ({path}): the program's import graph does not reach it, "
        "so its fences do not block the compile"
    )


def _catalogued_note(name: str, path: Path) -> str:
    return (
        f"note: unreached {name} ({path}): the program imports it but the entry never reaches it, "
        "so its fences do not block the compile"
    )


def _dep_distribution(module: str, dep_lane_report: dict[str, dict[str, str]] | None) -> str:
    """The distribution that ships a dependency module.

    The lane report is keyed by import name (issue #398), and a dependency
    module's top-level package is the import name the distribution ships
    under, so the head of the dotted name finds the distribution.
    """

    if not dep_lane_report:
        return ""
    entry = dep_lane_report.get(module.split(".", 1)[0])
    if entry is None:
        return ""
    return entry.get("distribution", "")


def _mark_dep_fences(
    fences: list[dict[str, Any]],
    units: list[tuple[str, Path, bool]],
    dep_names: set[str],
    dep_lane_report: dict[str, dict[str, str]] | None,
) -> list[dict[str, Any]]:
    """Name the distribution on a fence that comes from a dependency.

    A fence inside a reached dependency refuses the compile, so the user
    needs to know which dependency to fix: the fence keeps its own code
    and gains the distribution that ships the offending module.
    """

    if not dep_names or not dep_lane_report:
        return fences
    by_file: dict[str, str] = {}
    for name, path, _is_package in units:
        if name in dep_names:
            by_file[str(path)] = name
    if not by_file:
        return fences
    out: list[dict[str, Any]] = []
    for fence in fences:
        span = fence.get("span") or {}
        module = by_file.get(span.get("file", ""))
        if module is None:
            out.append(fence)
            continue
        distribution = _dep_distribution(module, dep_lane_report)
        message = fence.get("message", "")
        if distribution and distribution not in message:
            fence = {**fence, "message": f"{message} ({distribution} ships {module})"}
        out.append(fence)
    return out


def _program_plan(
    units: list[tuple[str, Path, bool]],
    exports: dict[str, dict[str, dict[str, Any]]],
    entry: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, list[str]]]]:
    """The program's import graph: cycles refused, init owners assigned.

    CPython runs a module body once, at the first import that reaches it.
    The owner map says which module triggers each body, so the init call
    lands in the module whose import statement performs it. A cycle is
    refused with the path that closes it.
    """

    trees: dict[str, ast.Module] = {}
    files: dict[str, Path] = {}
    for name, path, _ in units:
        trees[name] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        files[name] = path
    packages = {name: is_package for name, _, is_package in units}
    edges: dict[str, list[str]] = {name: _program_imports(trees[name], exports, name, packages[name]) for name in trees}
    # The statement that imports a module, so a cycle fence points at it.
    sites: dict[tuple[str, str], ast.stmt] = {}
    for name, tree in trees.items():
        for stmt in tree.body:
            for module, _local in _statement_imports(stmt, exports):
                sites.setdefault((name, module), stmt)
            for module in _relative_statement_imports(stmt, exports, name, packages[name]):
                sites.setdefault((name, module), stmt)
    # Only a module with script statements has an `__init__` export: its body
    # runs at import. A definition-only module contributes nothing.
    inited = {name for name, bucket in exports.items() if "__init__" in bucket}

    fences: list[dict[str, Any]] = []
    owners: dict[str, dict[str, list[str]]] = {name: [] for name in trees}
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(name: str) -> None:
        state[name] = 1
        stack.append(name)
        for child in edges.get(name, []):
            if child not in trees:
                continue
            if state.get(child) == 1:
                cycle = stack[stack.index(child) :] + [child]
                if _is_package_submodule_cycle(cycle, packages):
                    continue
                fences.append(
                    {
                        "code": "PS2002",
                        "message": f"circular import: {' -> '.join(cycle)}",
                        "span": _cycle_span(files[name], sites.get((name, child))),
                        "function": None,
                    }
                )
                continue
            if state.get(child) == 2:
                continue
            if child in inited:
                owners[name].append(child)
            visit(child)
        stack.pop()
        state[name] = 2

    for name in _program_roots(trees, entry):
        if state.get(name) is None:
            visit(name)
    if fences:
        return fences, {}
    return fences, owners


def _program_roots(trees: dict[str, ast.Module], entry: str | None = None) -> list[str]:
    """The order the graph walk starts in.

    The program's entry comes first: CPython runs it, and it is what
    reaches the other modules. The rest follow in name order.
    """

    roots = ["__main__"] if "__main__" in trees else []
    if entry is not None and entry in trees:
        roots = [entry] + [name for name in roots if name != entry]
    roots.extend(sorted(name for name in trees if name not in roots))
    return roots


def _is_package_submodule_cycle(cycle: list[str], packages: dict[str, bool]) -> bool:
    """True when a package unit imports its own submodule.

    A package unit's `__init__.py` exposing its submodules (or a submodule
    importing symbols from its containing package) forms an apparent cycle
    in the file-level import graph, but CPython runs the package body first
    and the submodule body within it without a cycle error.
    """
    for node in cycle:
        if packages.get(node):
            if all(m == node or m.startswith(node + ".") for m in cycle):
                return True
    return False


def _cycle_span(path: Path, stmt: ast.stmt | None) -> dict[str, Any]:
    """The span of the import statement that closes a cycle."""

    line = getattr(stmt, "lineno", 1) or 1
    col = getattr(stmt, "col_offset", 0) or 0
    end_line = getattr(stmt, "end_lineno", line) or line
    end_col = getattr(stmt, "end_col_offset", col) or col
    return {
        "file": str(path),
        "line": line,
        "col": col + 1,
        "endLine": end_line,
        "endCol": end_col + 1,
    }


def _program_imports(
    tree: ast.Module,
    exports: dict[str, dict[str, dict[str, Any]]],
    module: str = "",
    is_package: bool = False,
) -> list[str]:
    """The program's own modules that this module imports, in source order.

    Relative imports (project mode's package units) resolve against the
    importing module's package: ``from . import x`` in ``pkg.a`` reaches
    ``pkg.x`` and, through it, ``pkg``'s own body.
    """

    found: list[str] = []

    def note(target: str) -> None:
        if target in exports and target not in found:
            found.append(target)

    def resolve(relative: ast.ImportFrom) -> list[str]:
        base = _base_of(module, is_package, relative.level)
        if base is None:
            return []
        base_name = ".".join(base)
        targets: list[str] = []
        if relative.module:
            dotted = ".".join([*base, *relative.module.split(".")])
            targets.append(dotted)
            targets.extend(f"{dotted}.{alias.name}" for alias in relative.names)
        else:
            for alias in relative.names:
                submodule = f"{base_name}.{alias.name}"
                if submodule in exports:
                    targets.append(submodule)
                elif base_name != module:
                    targets.append(base_name)
        return targets

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in exports:
                    note(alias.name)
                else:
                    head = alias.name.split(".", 1)[0]
                    if head in exports:
                        note(head)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                if node.module in exports:
                    note(node.module)
                for alias in node.names:
                    child = f"{node.module}.{alias.name}"
                    if child in exports:
                        note(child)
                    elif (
                        node.module in exports
                        and alias.name not in exports[node.module]
                        and alias.name in {e.split(".")[-1] for e in exports if e.startswith(node.module + ".")}
                    ):
                        # CPython's submodule fallback (`from pkg.sub import
                        # blob` imports `pkg.sub.blob`): the import runs that
                        # module's body, so the reach edge exists.
                        note(child)
            elif node.level and module:
                for target in resolve(node):
                    note(target)
    return found


def _base_of(module: str, is_package: bool, level: int) -> list[str] | None:
    """The package path a relative import resolves against, or None.

    Same shape as the lowerer's `_package_base`: level 1 is the containing
    package (or the module itself when it is a package), each extra level
    climbs one parent.
    """

    if level < 1 or not module:
        return None
    bits = module.split(".")
    if not is_package:
        bits = bits[:-1]
    drop = level - 1
    if drop > len(bits):
        return None
    return bits[: len(bits) - drop]


def _relative_statement_imports(
    stmt: ast.stmt,
    exports: dict[str, dict[str, dict[str, Any]]],
    module: str,
    is_package: bool,
) -> list[str]:
    """The program modules one relative import statement reaches."""

    if not (isinstance(stmt, ast.ImportFrom) and stmt.level and module):
        return []
    return [
        target
        for target in _program_imports(ast.Module(body=[stmt], type_ignores=[]), exports, module, is_package)
        if target in exports
    ]


def _statement_imports(stmt: ast.stmt, exports: dict[str, dict[str, dict[str, Any]]]) -> list[tuple[str, str]]:
    """The program modules one import statement brings in.

    Each entry is the module and the local name the import binds for it, so
    the init call can be spelled the way the importing module sees it.
    """

    found: list[tuple[str, str]] = []

    def note(module: str, local: str) -> None:
        if module not in exports:
            return
        if any(item[0] == module for item in found):
            return
        found.append((module, local))

    if isinstance(stmt, ast.Import):
        for alias in stmt.names:
            if alias.asname:
                note(alias.name, alias.asname)
                continue
            if alias.name in exports:
                note(alias.name, alias.name.split(".")[0])
                continue
            head = alias.name.split(".", 1)[0]
            if head in exports:
                note(head, head)
        return found
    if isinstance(stmt, ast.ImportFrom) and stmt.level == 0 and stmt.module:
        note(stmt.module, stmt.module.split(".")[0])
        for alias in stmt.names:
            note(f"{stmt.module}.{alias.name}", alias.asname or alias.name)
    return found


def _inject_inits(
    tree: ast.Module,
    owned: list[str],
    exports: dict[str, dict[str, dict[str, Any]]],
    module: str = "",
    is_package: bool = False,
) -> ast.Module:
    """Call the init of every module this one first imports.

    CPython runs an imported module's body at the import statement, so the
    call goes right after the statement that triggers it. A module this one
    does not own (another module already imported it) is left alone: its
    body already ran. A relative import (project mode) spells the call with
    the resolved dotted module name.
    """

    if not owned:
        return tree
    body: list[ast.stmt] = []
    done: set[str] = set()

    def emit(target: str, local: str, stmt: ast.stmt) -> None:
        if target not in owned or target in done:
            return
        done.add(target)
        body.append(_init_call(local, stmt))

    for stmt in tree.body:
        body.append(stmt)
        for target, local in _statement_imports(stmt, exports):
            emit(target, local, stmt)
        for target in _relative_statement_imports(stmt, exports, module, is_package):
            emit(target, target.split(".")[-1], stmt)
    tree.body = body
    ast.fix_missing_locations(tree)
    return tree


def _init_call(local: str, anchor: ast.stmt) -> ast.stmt:
    """`local.__init__()` as a statement, placed at the anchor's position."""

    call = ast.Call(
        func=ast.Attribute(
            value=ast.Name(id=local, ctx=ast.Load()),
            attr="__init__",
            ctx=ast.Load(),
        ),
        args=[],
        keywords=[],
    )
    ast.copy_location(call, anchor)
    expr = ast.Expr(value=call)
    ast.copy_location(expr, anchor)
    return expr


def _catalog(
    units: list[tuple[str, Path, bool]],
    program: bool = False,
    entry: str | None = None,
    target: str | None = None,
    config: Any = None,
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, dict[str, dict[str, Any]]]]:
    """Repeat until a subclass can see a base exported by an earlier pass."""

    exports: dict[str, dict[str, dict[str, Any]]] = {}
    failed: dict[str, dict[str, dict[str, Any]]] = {}
    for _ in range(len(units) + 1):
        nxt, nxt_failed = _catalog_once(units, exports, program, entry=entry, target=target, config=config)
        if nxt == exports:
            return nxt, nxt_failed
        exports, failed = nxt, nxt_failed
    return exports, failed


def _catalog_once(
    units: list[tuple[str, Path, bool]],
    exports: dict[str, dict[str, dict[str, Any]]],
    program: bool = False,
    entry: str | None = None,
    target: str | None = None,
    config: Any = None,
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, dict[str, dict[str, Any]]]]:
    found: dict[str, dict[str, dict[str, Any]]] = {}
    failed: dict[str, dict[str, dict[str, Any]]] = {}
    exc_n = USER_EXC_BASE
    for name, path, is_package in units:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        lowerer = Lowerer(str(path), text, target=target, config=config)
        lowerer.module_name = name
        lowerer.is_package = is_package
        lowerer.cataloguing = True
        lowerer.package = exports
        lowerer.exc_n = exc_n
        lowerer.program = program
        lowerer.module_init = program and name != "__main__" and name != entry
        lowerer.entry_module = entry is not None and name == entry
        module = lowerer.module(tree)
        exc_n = lowerer.exc_n
        found[name] = _exports_of(name, lowerer)
        # A re-export in an `__init__.py` (`from .core import where`,
        # issue #405) makes the name part of the package's public surface,
        # so `from pkg import where` binds the way it does in CPython.
        for local, item in _reexports_of(name, tree, exports, is_package).items():
            if local not in found[name]:
                found[name][local] = item
        missed = _unexported_definitions(tree, lowerer, module["fences"])
        if missed:
            failed[name] = missed
    return found, failed


def _exports_of(module_name: str, lowerer: Lowerer) -> dict[str, dict[str, Any]]:
    bucket: dict[str, dict[str, Any]] = {}
    for fn, (params, ret) in lowerer.sigs.items():
        bucket[fn] = {
            "kind": "func",
            "ir_name": f"{module_name}.{fn}",
            "params": copy.deepcopy(params),
            "return": copy.deepcopy(ret),
        }
    # boxed:begin
    # Refs #641: the export table carries the defining module's island plan.
    # A definition the module planned for the island has no lowered body in
    # the merged module, so a cross-module call site has to know that before
    # it lowers a call to a name that is not there.
    for fn in lowerer.island_fns:
        if fn in bucket:
            bucket[fn]["island"] = True
    # boxed:end
    for const_name, value in lowerer.constants.items():
        bucket[const_name] = {"kind": "const", "value": copy.deepcopy(value)}
    for class_name, info in lowerer.classes.items():
        if info.get("synthetic") or info.get("imported"):
            continue
        bucket[class_name] = {"kind": "class", "name": class_name, "info": _class_export(info)}
    # A re-export bound from a sibling (`from .core import where` in the
    # package `__init__.py`, issue #398): the name lands in `pkg_funcs`, so
    # it becomes the package's own export and `from pkg import where`
    # resolves the same way it does in CPython.
    for local, item in lowerer.pkg_funcs.items():
        if local not in bucket:
            bucket[local] = copy.deepcopy(item)
    return bucket


def _reexports_of(
    module_name: str,
    tree: ast.Module,
    exports: dict[str, dict[str, dict[str, Any]]],
    is_package: bool,
) -> dict[str, dict[str, Any]]:
    """The names an `__init__.py` re-exports from a sibling module.

    A package's `__init__.py` is the package's public face: `from .core
    import where` makes `where` a name of the package, so `from certifi
    import where` binds it. The lowerer binds the name in the importing
    module, but the export table is what a consumer resolves against, so
    the alias is recorded here.

    The alias is a pointer, not a copy: the defining module still emits the
    function or class, and the entry keeps the defining module's
    `ir_name`. An alias chain (`pkg/__init__.py` re-exports from
    `pkg.sub`, whose own `__init__.py` re-exports again) resolves through
    the table the catalog fixpoint builds, so the chain lands in one pass
    per fixpoint round.
    """

    if not is_package:
        return {}
    bucket: dict[str, dict[str, Any]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue
        base = _base_of(module_name, is_package, node.level)
        if base is None:
            continue
        if node.module:
            # `from .core import where`: the sibling module holds the name.
            source = ".".join([*base, *node.module.split(".")])
            for alias in node.names:
                if alias.name == "*":
                    continue
                item = _alias_item(exports, source, alias.name)
                if item is not None:
                    bucket[alias.asname or alias.name] = item
            continue
        # `from . import core`: the name is the submodule itself.
        for alias in node.names:
            if alias.name == "*":
                continue
            source = ".".join([*base, alias.name])
            if source in exports:
                bucket[alias.asname or alias.name] = {"kind": "module", "module": source}
    return bucket


def _alias_item(exports: dict[str, dict[str, dict[str, Any]]], module: str, name: str) -> dict[str, Any] | None:
    """The export `name` of `module`, or None when it is not exported.

    The entry is copied, so the alias carries the defining module's
    `ir_name`: the defining module still emits the function or class.
    """

    item = exports.get(module, {}).get(name)
    if item is None:
        return None
    return copy.deepcopy(item)


def _class_export(info: dict[str, Any]) -> dict[str, Any]:
    """Class shape without the AST node. The defining module still emits the class."""

    exported: dict[str, Any] = {
        "fields": copy.deepcopy(info.get("fields", [])),
        "own_fields": copy.deepcopy(info.get("own_fields", [])),
        "methods": copy.deepcopy(info.get("methods", {})),
        "method_kind": dict(info.get("method_kind", {})),
        "method_owner": dict(info.get("method_owner", {})),
        "base": info.get("base"),
        "imported": True,
    }
    if info.get("generated_init") is not None:
        exported["generated_init"] = info["generated_init"]
    if info.get("exception"):
        exported["exception"] = True
        exported["exc_code"] = info.get("exc_code")
    if info.get("enum"):
        exported["enum"] = True
        exported["members"] = dict(info.get("members") or {})
    if info.get("coro"):
        # A coroutine frame class (issue #178, stage B). The consumer needs
        # the kind to tell a frame from a user class, and `coro_elem` (the
        # coroutine's result type) to know what the awaited value's type is.
        exported["coro"] = info["coro"]
        if info.get("coro_elem") is not None:
            exported["coro_elem"] = copy.deepcopy(info["coro_elem"])
    return exported


def _is_program_dir(root: Path) -> bool:
    """True when the directory is a program.

    Two shapes infer a program: a root ``__main__.py`` (the ``python DIR``
    and ``python -m DIR`` entry), or a root ``main.py`` with no
    ``__init__.py`` (``main.py`` plus sibling modules, the other R2 shape).
    A root ``__init__.py`` without either stays a library package.
    """

    root = root.resolve()
    if (root / "__main__.py").is_file() and not (root / "__init__.py").is_file():
        return True
    return (root / "main.py").is_file() and not (root / "__init__.py").is_file()


def _program_entry_file(root: Path) -> Path | None:
    """The inferred entry file: ``__main__.py`` first, then ``main.py``."""

    root = root.resolve()
    if (root / "__main__.py").is_file():
        return root / "__main__.py"
    if (root / "main.py").is_file() and not (root / "__init__.py").is_file():
        return root / "main.py"
    return None


def _entry_module_file(root: Path, module: str) -> Path:
    """The file of an explicitly named entry module (project mode).

    ``module`` is the console script's dotted module path relative to the
    project root; ``root`` here is the package directory the walk covers,
    so the tail of the path resolves inside it (``mylib.hello`` walked from
    ``src/mylib`` is ``hello.py``).
    """

    parts = module.split(".")
    # The walk root is the entry's package (`src/mylib` for `mylib.hello`),
    # so the module's own name is the tail of its path. A package entry
    # (`app` walked from `src/app`) is the root's own `__init__.py`.
    name = parts[-1]
    if (root / "__init__.py").is_file() and root.name == name:
        return root / "__init__.py"
    candidate = root / f"{name}.py"
    if candidate.is_file():
        return candidate
    package = root / name / "__init__.py"
    if package.is_file():
        return package
    raise IRError(f"entry module {module} not found in {root}")


def _discover_dep_roots(roots: list[Path]) -> tuple[list[tuple[str, Path, bool]], list[dict[str, Any]]]:
    """The module units of the dependency source roots (issue #398).

    Each root is one unpacked pure wheel's import root (`deps.py` cache
    `src/<sha256>/`), so its top-level packages and modules import under
    their plain names, the way `sys.path` resolves them. A root that is not
    an identifier (a wheel shipping a non-identifier top-level name) is
    refused with PS3101. The discovery is structural, before any lowering:
    a wheel that cannot be named is refused whatever the program imports,
    while a module-level fence only refuses when the program reaches that
    module (issue #424, the pruning in `lower_directory`).
    """

    units: list[tuple[str, Path, bool]] = []
    for root in roots:
        root = root.resolve()
        if not root.is_dir():
            return [], [_dep_fence(f"dependency source root {root} does not exist")]
        for path in sorted(root.iterdir()):
            if path.name.startswith(".") or path.name == "__pycache__":
                continue
            if path.suffix == ".py":
                if not path.stem.isidentifier():
                    return [], [_dep_name_fence(path.name)]
                units.append((path.stem, path, False))
            elif (path / "__init__.py").is_file():
                if not path.name.isidentifier():
                    return [], [_dep_name_fence(path.name)]
                units.extend(_discover_dep_package(path, path.name))
    return units, []


def _discover_dep_package(package_dir: Path, name: str) -> list[tuple[str, Path, bool]]:
    """The units of one dependency package, dotted under ``name``."""

    units: list[tuple[str, Path, bool]] = [(name, package_dir / "__init__.py", True)]
    for path in sorted(package_dir.rglob("*.py")):
        relative = path.relative_to(package_dir)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        if path.name == "__init__.py":
            # An intermediate package is its own importable unit: a program
            # that imports `pkg.sub` (or reads `files("pkg.sub")`) binds the
            # package body the way CPython runs it, so the unit exists even
            # when nothing else inside it is reached.
            parts = relative.parts[:-1]
            if parts:
                units.append((".".join((name, *parts)), path, True))
            continue
        stem = relative.with_suffix("")
        if not all(part.isidentifier() for part in stem.parts):
            continue
        units.append((".".join((name, *stem.parts)), path, False))
    return units


def _dep_fence(message: str) -> dict[str, Any]:
    """A PS3101 refusal fence outside any source span."""

    return {"code": "PS3101", "message": message, "span": None, "function": None}


def _dep_name_fence(name: str) -> dict[str, Any]:
    return _dep_fence(
        f"{name} is not a valid module name (a dependency wheel ships a top-level name the compiler cannot import)"
    )


def _discover(
    root: Path, program_mode: bool = False, entry: str | None = None, prefix: str = ""
) -> tuple[list[tuple[str, Path, bool]], list[str]]:
    """Find every module in the tree.

    A directory whose root holds ``__main__.py`` and no ``__init__.py`` is a
    program: that file is the entry point, and every sibling module is
    reachable under its plain name. Any other directory is a package, where
    the directory name is the module root and ``__main__.py`` is the
    ``python -m`` script, not an importable module.

    With ``program_mode`` the caller asked for the ``python -m pkg`` shape:
    the root ``__main__.py`` is the entry even when the directory is a
    package, and the modules keep the package namespace.

    With ``entry`` (project mode) the caller names the entry module by its
    path relative to the root. That module keeps its own dotted name (its
    relative imports resolve against it), and the tree keeps the package
    namespace.
    """

    root = root.resolve()
    package = prefix or root.name
    if not package.isidentifier():
        raise IRError(f"package directory {package} is not an identifier")
    program = program_mode or _is_program_dir(root)
    namespaced = program_mode and not _is_program_dir(root)
    if entry is not None:
        namespaced = True
        program = True
        entry_file = _entry_module_file(root, entry)
    else:
        entry_file = _program_entry_file(root)
    found: list[tuple[str, Path, bool]] = []
    skipped: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        if program and entry is None and entry_file is not None and path == entry_file:
            # The program entry. It lowers as the `__main__` module.
            found.append(("__main__", path, False))
            continue
        if path.name == "__main__.py":
            skipped.append(relative.as_posix())
            continue
        if path.name == "__init__.py":
            parts = relative.parts[:-1]
            if program and not namespaced:
                # A program sub-package: `pkg/__init__.py` is the module
                # `pkg`, imported by its plain name.
                found.append((".".join(parts), path, True))
                continue
            name = ".".join((package, *parts)) if parts else package
            found.append((name, path, True))
            continue
        stem = relative.with_suffix("")
        if not all(part.isidentifier() for part in stem.parts):
            raise IRError(f"{relative} is not a module name")
        if program and not namespaced:
            # A program sibling is imported by its plain name.
            found.append((".".join(stem.parts), path, False))
            continue
        found.append(((".".join((package, *stem.parts))), path, False))
    if not found:
        raise IRError(f"no Python files in {root}")
    return found, skipped


def _skip_note(root: Path, relative: str) -> str:
    return f"note: skipped {root.name}/{relative}; __main__.py is the python -m script, not an importable module"


_NOT_EXPORTED = " is not exported by this package"


def _promote_definition_fences(
    fences: list[dict[str, Any]], failed: dict[str, dict[str, dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Report the fence inside a class or function before the import that missed it."""

    by_qual = {f"{module}.{name}": fence for module, names in failed.items() for name, fence in names.items()}
    if not by_qual:
        return fences

    def ident(fence: dict[str, Any]) -> tuple[Any, ...]:
        span = fence.get("span") or {}
        return (fence.get("code"), fence.get("message"), span.get("file"), span.get("line"), span.get("col"))

    wanted = {ident(fence) for fence in by_qual.values()}
    emitted: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for fence in fences:
        message = fence.get("message") or ""
        if message.endswith(_NOT_EXPORTED):
            defined = by_qual.get(message[: -len(_NOT_EXPORTED)])
            if defined is not None:
                mark = ident(defined)
                if mark not in emitted:
                    out.append(defined)
                    emitted.add(mark)
        mark = ident(fence)
        if mark in emitted and mark in wanted:
            continue
        out.append(fence)
        if mark in wanted:
            emitted.add(mark)
    return out


def _unexported_definitions(
    tree: ast.Module, lowerer: Lowerer, fences: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    exported = set(lowerer.sigs)
    for class_name, info in lowerer.classes.items():
        if info.get("synthetic") or info.get("imported"):
            continue
        exported.add(class_name)
    found: dict[str, dict[str, Any]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if node.name in exported:
            continue
        start = node.lineno
        decorators = getattr(node, "decorator_list", None) or []
        if decorators:
            start = min(start, min(item.lineno for item in decorators))
        end = node.end_lineno or node.lineno
        inside = [fence for fence in fences if start <= fence["span"]["line"] <= end]
        if inside:
            found[node.name] = inside[0]
    return found
