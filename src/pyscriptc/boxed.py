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
    for fence in fences:
        owner = fence.get("function") or fence.get("definition")
        if owner is not None:
            fenced.add(owner)
        elif fence.get("code") in ("PS2001", "PS2002"):
            top_import_fences += 1

    if not top_level_island and top_import_fences > 0:
        top_level_island = True
    planes = {}
    for record in functions:
        name = record["name"] if isinstance(record, dict) else str(record)
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
        name = record["name"] if isinstance(record, dict) else str(record)
        decided = record.get("island") if isinstance(record, dict) else None
        if decided is not None:
            is_island = bool(decided)
        else:
            is_island = top_level_island or name in fenced or any(owner.startswith(f"{name}.") for owner in fenced)
        class_planes[name] = ISLAND if is_island else NATIVE
        if is_island:
            island_classes.add(name)

    demotions: dict[str, str] = dict(demoted_by or {})
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

    return [name for name, plane in planes.get("functions", {}).items() if plane == ISLAND]


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
        if not candidate.is_absolute() and tmp_dir is not None:
            candidate = (tmp_dir / candidate).resolve()
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

    fns = plane_map.get("functions", {})
    cls = plane_map.get("classes", {})
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
