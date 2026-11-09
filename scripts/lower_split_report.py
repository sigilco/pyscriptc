#!/usr/bin/env python3
"""Report on a lower/ module to plan a split into mixin modules.

Reads a Python module with the stdlib `ast` module only (no new deps) and
reports, per class:

- per method: line count, `self` attributes read and written, other methods
  called on `self` (the call graph), and module-level names used.
- clusters: connected components of the call graph weighted by shared
  attributes, with total lines, entry points called from outside the cluster,
  and the shared state the cluster needs.
- suggested cut sets that bring the file under the line target with the fewest
  cross-cluster edges.
- methods over the long-method threshold, cyclomatic complexity per method,
  import cycles among the lower/ package, and unused module-level names.

Usage:
    uv run python scripts/lower_split_report.py src/pyscriptc/lower/__init__.py
    uv run python scripts/lower_split_report.py src/pyscriptc/lower/__init__.py --json
"""

from __future__ import annotations

import argparse
import ast
import builtins
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TARGET_LINES = 1500
DEFAULT_LONG_METHOD = 150


# ---------------------------------------------------------------------------
# data model
# ---------------------------------------------------------------------------


@dataclass
class MethodInfo:
    """One method of one class in the analysed module."""

    cls: str
    name: str
    lineno: int
    end_lineno: int
    lines: int
    attrs_read: set[str] = field(default_factory=set)
    attrs_written: set[str] = field(default_factory=set)
    calls_self: set[str] = field(default_factory=set)
    module_names: set[str] = field(default_factory=set)
    complexity: int = 1

    @property
    def qualname(self) -> str:
        return f"{self.cls}.{self.name}"


@dataclass
class ModuleReport:
    """Everything the script knows about one analysed module."""

    path: Path
    module_name: str
    total_lines: int
    module_level_names: set[str] = field(default_factory=set)
    used_module_names: set[str] = field(default_factory=set)
    methods: list[MethodInfo] = field(default_factory=list)
    classes: dict[str, list[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# parsing helpers
# ---------------------------------------------------------------------------


def _iter_methods(tree: ast.Module) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Return (class name, function node) for every method of every class."""
    out: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append((node.name, child))
    return out


def _self_is(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "self"


def _attr_chain(node: ast.expr) -> tuple[str, ...] | None:
    """Return the dotted name of an attribute chain, or None if not pure."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return tuple(reversed(parts))
    return None


def _complexity(node: ast.AST) -> int:
    """McCabe cyclomatic complexity: 1 + one per decision point."""
    score = 1
    for child in ast.walk(node):
        if isinstance(
            child,
            (
                ast.If,
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.ExceptHandler,
                ast.With,
                ast.AsyncWith,
                ast.Assert,
                ast.comprehension,
            ),
        ):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
        elif isinstance(child, ast.IfExp):
            score += 1
        elif isinstance(child, ast.Match):
            score += max(len(case.patterns) for case in child.cases)
    return score


def _module_level_names(tree: ast.Module) -> set[str]:
    """Top-level names a module binds: imports, assignments, defs, classes."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            names.add(elt.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


_MUTATING_METHODS = frozenset(
    {
        "add",
        "append",
        "clear",
        "discard",
        "extend",
        "insert",
        "pop",
        "popitem",
        "remove",
        "reverse",
        "setdefault",
        "sort",
        "update",
        "update_or_create",
        "appendleft",
        "extendleft",
        "clear_cache",
    }
)


def _root_self_attr(node: ast.expr) -> str | None:
    """The `self` attribute a write target roots at, if any.

    `self.x = 1` roots at x, `self.x[k] = 1` roots at x, `self.x.y = 1`
    roots at x: all of them mutate the attribute named first after `self`.
    """
    cur = node
    while True:
        if isinstance(cur, ast.Subscript):
            cur = cur.value
            continue
        if isinstance(cur, ast.Attribute):
            if _self_is(cur.value):
                return cur.attr
            cur = cur.value
            continue
        return None


def _collect_method_info(cls: str, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> MethodInfo:
    """Gather attribute reads/writes, self calls and module names for a method."""
    info = MethodInfo(
        cls=cls,
        name=fn.name,
        lineno=fn.lineno,
        end_lineno=fn.end_lineno or fn.lineno,
        lines=(fn.end_lineno or fn.lineno) - fn.lineno + 1,
        complexity=_complexity(fn),
    )
    # Attribute writes: `self.x = ...`, `self.x[k] = ...`, `self.x.y = ...`.
    for node in ast.walk(fn):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            else:
                targets = [node.target]
            for target in targets:
                root = _root_self_attr(target)
                if root is not None:
                    info.attrs_written.add(root)
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                root = _root_self_attr(target)
                if root is not None:
                    info.attrs_written.add(root)
    # Attribute reads and self method calls.
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute):
            if _self_is(node.value):
                info.attrs_read.add(node.attr)
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and _self_is(func.value):
                info.calls_self.add(func.attr)
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute):
                # self.container.mutator(...) mutates the container.
                if _self_is(func.value.value) and func.attr in _MUTATING_METHODS:
                    info.attrs_written.add(func.value.attr)
            elif isinstance(func, ast.Name) and func.id in {"getattr", "setattr", "hasattr"}:
                for arg in node.args[1:2]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        info.attrs_read.add(arg.value)
                        if func.id == "setattr":
                            info.attrs_written.add(arg.value)
    # Module-level names used (bare Name loads that are not locals).
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            info.module_names.add(node.id)
    return info


def _local_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Names bound inside the function (params, locals, comprehensions)."""
    names: set[str] = set()
    args = fn.args
    for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
        names.add(arg.arg)
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.comprehension, ast.arguments)):
            for sub in ast.walk(node):
                if isinstance(sub, ast.arg):
                    names.add(sub.arg)
    return names


def analyse_module(path: Path) -> ModuleReport:
    """Parse one module and collect the per-method facts."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    module_level = _module_level_names(tree)
    report = ModuleReport(
        path=path,
        module_name=path.stem if path.name != "__init__.py" else path.parent.name,
        total_lines=len(source.splitlines()),
        module_level_names=module_level,
    )
    for cls, fn in _iter_methods(tree):
        info = _collect_method_info(cls, fn)
        locals_ = _local_names(fn)
        # A module-level name used by the method is a bare load that is not a
        # local, not a builtin, and not a dunder.
        info.module_names = {
            n for n in info.module_names if n not in locals_ and n not in _BUILTIN_NAMES and not n.startswith("__")
        }
        report.methods.append(info)
        report.classes.setdefault(cls, []).append(info.name)
    used: set[str] = set()
    for info in report.methods:
        used |= info.module_names
    # Module-level names used outside class methods too (module functions).
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            locals_ = _local_names(node)
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    if sub.id not in locals_ and sub.id not in _BUILTIN_NAMES:
                        used.add(sub.id)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            # Class bases like `class Lowerer(A, B)` read the imported mixins.
            value = node.value
            if isinstance(value, (ast.Tuple, ast.List)):
                for elt in value.elts:
                    if isinstance(elt, ast.Name):
                        used.add(elt.id)
            elif isinstance(value, ast.Name):
                used.add(value.id)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                if isinstance(base, ast.Name):
                    used.add(base.id)
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Name):
                    used.add(decorator.id)
    report.used_module_names = used & module_level
    return report


_BUILTIN_NAMES = frozenset(dir(builtins)) | {
    "self",
    "cls",
    "__name__",
    "__file__",
    "__doc__",
    "__package__",
    "__spec__",
    "__builtins__",
    "__annotations__",
    "__class__",
}


# ---------------------------------------------------------------------------
# clustering
# ---------------------------------------------------------------------------


def _union_find(groups: int) -> tuple[list[int], int]:
    parent = list(range(groups))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    return parent, find  # type: ignore[return-value]


def cluster_methods(
    methods: list[MethodInfo],
    attr_weight: float = 0.5,
    call_link: bool = True,
    attr_link: bool = True,
    exclude_roots: bool = True,
) -> list[list[MethodInfo]]:
    """Cluster methods into cohesive groups.

    Two methods are linked when one calls the other (direct call edge) or when
    they share an attribute (weighted link). The groups are the connected
    components of that graph. A method with no links forms its own group.

    Entry points (methods no same-class method calls: the public surface of
    the class) are not linked to their callees. Without that, one entry point
    that calls everything fuses the whole class into a single component and
    the clustering says nothing. The orca repo hit the same shape: one
    "spine" method family fused 212 methods, and removing the connectors gave
    20 clean sub-clusters. The entry points are reported separately as the
    callers that reach each cluster.

    The attribute link uses a relative threshold: an attribute joins its
    readers only when `owners / total attribute slots` is at or above
    `attr_weight`, so hub attributes read by everyone (a `Lowerer.temp` that
    70 of 86 methods touch) do not fuse the whole class into one component.

    With `attr_link` off, only direct self-calls link methods: that is the
    stricter view used when a cluster must be a closed family (the moved
    module calls nothing it left behind). Both views are reported.
    """
    by_name: dict[str, list[int]] = defaultdict(list)
    for i, m in enumerate(methods):
        by_name[m.name].append(i)
    root_names = {m.name for m in roots(methods)}
    parent, find = _union_find(len(methods))
    if call_link:
        for i, m in enumerate(methods):
            if exclude_roots and m.name in root_names:
                continue
            for callee in m.calls_self:
                for j in by_name.get(callee, []):
                    if methods[j].cls == m.cls:
                        parent[find(i)] = find(j)
    if attr_link:
        attr_owner: dict[str, list[int]] = defaultdict(list)
        for i, m in enumerate(methods):
            if exclude_roots and m.name in root_names:
                continue
            for attr in m.attrs_read | m.attrs_written:
                attr_owner[attr].append(i)
        for owners in attr_owner.values():
            if len(owners) < 2:
                continue
            total = sum(len(methods[o].attrs_read | methods[o].attrs_written) for o in owners)
            share = len(owners) / total if total else 0.0
            if share >= attr_weight:
                for other in owners[1:]:
                    parent[find(owners[0])] = find(other)
    groups: dict[int, list[MethodInfo]] = defaultdict(list)
    for i, m in enumerate(methods):
        groups[find(i)].append(m)
    return [groups[key] for key in sorted(groups, key=lambda k: -len(groups[k]))]


def roots(methods: list[MethodInfo]) -> list[MethodInfo]:
    """Methods of the class that no other method of the class calls.

    These are the class public surface plus helpers called only from outside
    the class (for example through a mixin sibling). `Lowerer.module` is one:
    the driver calls it, everything under it is called from inside the class.
    Dunders are never roots: `__init__` is called externally too but it is
    state setup, not a cut surface.
    """
    called: set[str] = set()
    for m in methods:
        called |= m.calls_self
    return [m for m in methods if m.name not in called and not m.name.startswith("__")]


def entry_points(methods: list[MethodInfo]) -> list[MethodInfo]:
    """Alias of roots kept for the call-site vocabulary."""
    return roots(methods)


def cluster_edges(clusters: list[list[MethodInfo]]) -> list[tuple[int, int, str]]:
    """Return (from cluster, to cluster, method) for every cross-cluster call."""
    owner: dict[str, int] = {}
    for ci, cluster in enumerate(clusters):
        for m in cluster:
            owner[f"{m.cls}.{m.name}"] = ci
    edges: list[tuple[int, int, str]] = []
    for ci, cluster in enumerate(clusters):
        for m in cluster:
            for callee in sorted(m.calls_self):
                key = f"{m.cls}.{callee}"
                if key in owner and owner[key] != ci:
                    edges.append((ci, owner[key], m.qualname))
    return edges


def cluster_entry_points(clusters: list[list[MethodInfo]], edges: list[tuple[int, int, str]]) -> dict[int, set[str]]:
    """Per cluster, the methods called from another cluster."""
    entries: dict[int, set[str]] = defaultdict(set)
    for _src, dst, caller in edges:
        entries[dst].add(caller)
    return entries


def cluster_state(clusters: list[list[MethodInfo]]) -> dict[int, set[str]]:
    """Per cluster, the attributes it touches."""
    state: dict[int, set[str]] = defaultdict(set)
    for ci, cluster in enumerate(clusters):
        for m in cluster:
            state[ci] |= m.attrs_read | m.attrs_written
    return state


def suggest_cuts(
    clusters: list[list[MethodInfo]],
    edges: list[tuple[int, int, str]],
    target_lines: int,
) -> list[dict[str, object]]:
    """Suggest cut sets that bring the file under the target with few edges.

    Greedy peel: the biggest cluster stays resident, the others are moved out
    cheapest first by (lines moved per cross-cluster edge). Every prefix of
    that order is a candidate cut set; the best candidate is the one that
    reaches the target with the fewest cross-cluster edges, and it is the
    last entry of the list.
    """
    cluster_lines = [sum(m.lines for m in c) for c in clusters]
    total = sum(cluster_lines)
    if total <= target_lines:
        return []
    edge_count: dict[int, int] = defaultdict(int)
    for src, dst, _ in edges:
        edge_count[src] += 1
        edge_count[dst] += 1
    resident = max(range(len(clusters)), key=lambda c: cluster_lines[c])
    order = sorted(
        (c for c in range(len(clusters)) if c != resident),
        key=lambda c: (-cluster_lines[c] / max(edge_count[c], 1), -cluster_lines[c]),
    )
    moved: list[int] = []
    cuts: list[dict[str, object]] = []
    for c in order:
        moved.append(c)
        remaining_lines = total - sum(cluster_lines[m] for m in moved)
        cuts.append(
            {
                "move_clusters": list(moved),
                "resident_cluster": resident,
                "remaining_lines": remaining_lines,
                "under_target": remaining_lines <= target_lines,
                "cross_edges": sum(1 for s, d, _ in edges if s in moved or d in moved),
                "moved_lines": total - remaining_lines,
            }
        )
    return cuts


def best_cut(cuts: list[dict[str, object]]) -> dict[str, object] | None:
    """The candidate cut set that reaches the target with the fewest edges."""
    if not cuts:
        return None
    feasible = [c for c in cuts if c["under_target"]]
    if not feasible:
        return None
    return min(feasible, key=lambda c: (c["cross_edges"], c["remaining_lines"]))


def state_owners(methods: list[MethodInfo]) -> dict[str, list[str]]:
    """Per attribute, the methods that write it."""
    owners: dict[str, list[str]] = defaultdict(list)
    for m in methods:
        for attr in sorted(m.attrs_written):
            owners[attr].append(m.qualname)
    return dict(owners)


def extraction_seeds(methods: list[MethodInfo], min_lines: int = 50) -> list[dict[str, object]]:
    """Rank methods as extraction seeds for a family move.

    A seed is a method big enough to be worth moving whose external fan-in
    (distinct same-class callers outside its own call-reachable closure) is
    low: moving it and its callees cuts lines with few new cross-module
    self-calls. Fan-in counts callers; fan-out counts the callees the seed
    would leave behind if moved alone.
    """
    by_name: dict[str, list[MethodInfo]] = defaultdict(list)
    for m in methods:
        by_name[m.name].append(m)
    callers: dict[str, set[str]] = defaultdict(set)
    for m in methods:
        for callee in m.calls_self:
            for target in by_name.get(callee, []):
                if target.cls == m.cls and target.name != m.name:
                    callers[target.name].add(m.name)
    seeds: list[dict[str, object]] = []
    for m in methods:
        if m.lines < min_lines or m.name == "__init__":
            continue
        fan_in = len(callers.get(m.name, ()))
        fan_out = len({c for c in m.calls_self if c in by_name})
        seeds.append(
            {
                "method": m.qualname,
                "lines": m.lines,
                "fan_in": fan_in,
                "fan_out": fan_out,
                "calls_self": sorted(m.calls_self),
                "attrs": sorted(m.attrs_read | m.attrs_written),
            }
        )
    seeds.sort(key=lambda s: (-int(s["lines"]), int(s["fan_in"])))
    return seeds


def init_only_state(methods: list[MethodInfo], init_name: str = "__init__") -> set[str]:
    """Attributes written only by the constructor (state set up once)."""
    owners = state_owners(methods)
    init_written: set[str] = set()
    other_written: set[str] = set()
    for attr, writers in owners.items():
        if all(w.endswith(f".{init_name}") for w in writers):
            init_written.add(attr)
        else:
            other_written.add(attr)
    return init_written


def package_usage(package_dir: Path, names: set[str]) -> dict[str, list[str]]:
    """Per module-level name, the sibling modules that reference it."""
    usage: dict[str, list[str]] = defaultdict(list)
    for path in sorted(package_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        local = _local_names_of_module(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id in names and node.id not in local:
                    usage[node.id].append(path.stem)
    return dict(usage)


# ---------------------------------------------------------------------------
# package level analyses
# ---------------------------------------------------------------------------


def _local_names_of_module(tree: ast.Module) -> set[str]:
    """Every name bound anywhere in the module (so imports do not count)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def import_cycles(package_dir: Path) -> list[list[str]]:
    """Find import cycles among the modules of a package directory."""
    modules: dict[str, Path] = {}
    for path in sorted(package_dir.glob("*.py")):
        modules[path.stem] = path
    edges: dict[str, set[str]] = defaultdict(set)
    for name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level and node.module:
                    # from .foo import x -> module "foo"
                    target = node.module.split(".")[0]
                    if target in modules:
                        edges[name].add(target)
                elif node.module and node.module.startswith("pyscriptc.lower."):
                    target = node.module.rsplit(".", 1)[-1]
                    if target in modules:
                        edges[name].add(target)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("pyscriptc.lower."):
                        target = alias.name.rsplit(".", 1)[-1]
                        if target in modules:
                            edges[name].add(target)
    cycles: list[list[str]] = []
    colour: dict[str, int] = {}

    def visit(node: str, stack: list[str]) -> None:
        colour[node] = 1
        stack.append(node)
        for nxt in sorted(edges.get(node, ())):
            if nxt not in colour:
                visit(nxt, stack)
            elif colour.get(nxt) == 1:
                cyc = stack[stack.index(nxt) :] + [nxt]
                if cyc not in cycles:
                    cycles.append(cyc)
        stack.pop()
        colour[node] = 2

    for name in sorted(modules):
        if name not in colour:
            visit(name, [])
    return cycles


def unused_module_names(report: ModuleReport) -> set[str]:
    """Module-level names never referenced inside the module."""
    return report.module_level_names - report.used_module_names


# ---------------------------------------------------------------------------
# text output
# ---------------------------------------------------------------------------


def format_text(report: ModuleReport, package_dir: Path | None, target: int, long_method: int) -> str:
    """Render the whole report as plain text."""
    out: list[str] = []
    out.append(f"module: {report.path}")
    out.append(f"lines: {report.total_lines} (target {target})")
    out.append("")

    out.append("== classes ==")
    for cls, names in report.classes.items():
        total = sum(m.lines for m in report.methods if m.cls == cls)
        out.append(f"  {cls}: {len(names)} methods, {total} lines")
    out.append("")

    out.append("== methods ==")
    for m in sorted(report.methods, key=lambda m: -m.lines):
        out.append(f"  {m.qualname} L{m.lineno}-{m.end_lineno} ({m.lines} lines, cc={m.complexity})")
        out.append(f"    reads: {', '.join(sorted(m.attrs_read)) or '-'}")
        out.append(f"    writes: {', '.join(sorted(m.attrs_written)) or '-'}")
        out.append(f"    calls: {', '.join(sorted(m.calls_self)) or '-'}")
        out.append(f"    module names: {', '.join(sorted(m.module_names)) or '-'}")

    clusters = cluster_methods(report.methods)
    edges = cluster_edges(clusters)
    entries = cluster_entry_points(clusters, edges)
    state = cluster_state(clusters)

    out.append("")
    out.append(f"== clusters (call+attribute, {len(clusters)}) ==")
    for ci, cluster in enumerate(clusters):
        lines = sum(m.lines for m in cluster)
        out.append(f"  cluster {ci}: {len(cluster)} methods, {lines} lines")
        for m in sorted(cluster, key=lambda m: m.lineno):
            out.append(f"    {m.qualname} ({m.lines} lines)")
        out.append(f"    entry points from outside: {', '.join(sorted(entries.get(ci, ()))) or '-'}")
        out.append(f"    shared state: {', '.join(sorted(state.get(ci, ()))) or '-'}")

    out.append("")
    out.append("== cross-cluster edges ==")
    if not edges:
        out.append("  (none)")
    for src, dst, caller in edges:
        out.append(f"  cluster {src} -> cluster {dst} via {caller}")

    out.append("")
    out.append("== clusters (call-only, stricter) ==")
    call_clusters = cluster_methods(report.methods, attr_link=False)
    call_edges = cluster_edges(call_clusters)
    call_entries = cluster_entry_points(call_clusters, call_edges)
    for ci, cluster in enumerate(call_clusters):
        lines = sum(m.lines for m in cluster)
        out.append(f"  cluster {ci}: {len(cluster)} methods, {lines} lines")
        out.append(f"    entry points from outside: {', '.join(sorted(call_entries.get(ci, ()))) or '-'}")

    out.append("")
    out.append("== suggested cuts ==")
    cuts = suggest_cuts(call_clusters, call_edges, target)
    if not cuts:
        out.append("  (file already under the target)")
    for cut in cuts:
        out.append(
            f"  move {cut['move_clusters']} -> {cut['remaining_lines']} lines left, "
            f"cross edges {cut['cross_edges']}, under target: {cut['under_target']}"
        )
    best = best_cut(cuts)
    if best is not None:
        out.append(
            f"  best: move clusters {best['move_clusters']} "
            f"({best['moved_lines']} lines out, {best['remaining_lines']} left, "
            f"{best['cross_edges']} cross edges)"
        )

    out.append("")
    out.append("== extraction seeds (big, low fan-in) ==")
    for seed in extraction_seeds(report.methods)[:20]:
        out.append(
            f"  {seed['method']}: {seed['lines']} lines, fan_in {seed['fan_in']}, "
            f"fan_out {seed['fan_out']}, calls {', '.join(seed['calls_self']) or '-'}"
        )

    out.append("")
    out.append(f"== methods over {long_method} lines ==")
    longs = [m for m in report.methods if m.lines > long_method]
    if not longs:
        out.append("  (none)")
    for m in sorted(longs, key=lambda m: -m.lines):
        out.append(f"  {m.qualname}: {m.lines} lines (L{m.lineno})")

    out.append("")
    out.append("== cyclomatic complexity (top 15) ==")
    for m in sorted(report.methods, key=lambda m: -m.complexity)[:15]:
        out.append(f"  {m.qualname}: {m.complexity}")

    if package_dir is not None:
        out.append("")
        out.append("== import cycles in package ==")
        cycles = import_cycles(package_dir)
        if not cycles:
            out.append("  (none)")
        for cyc in cycles:
            out.append("  " + " -> ".join(cyc))

    out.append("")
    out.append("== unused module-level names ==")
    unused = unused_module_names(report)
    if not unused:
        out.append("  (none)")
    for name in sorted(unused):
        out.append(f"  {name}")

    out.append("")
    out.append("== module-level names used by sibling modules ==")
    if package_dir is not None:
        usage = package_usage(package_dir, unused)
        for name in sorted(unused):
            users = sorted(set(usage.get(name, ())) - {report.path.stem})
            if users:
                out.append(f"  {name}: {', '.join(users)}")
    return "\n".join(out)


def to_json(report: ModuleReport, package_dir: Path | None, target: int, long_method: int) -> dict[str, object]:
    """Render the whole report as a JSON-able dict."""
    clusters = cluster_methods(report.methods)
    edges = cluster_edges(clusters)
    entries = cluster_entry_points(clusters, edges)
    state = cluster_state(clusters)
    call_clusters = cluster_methods(report.methods, attr_link=False)
    call_edges = cluster_edges(call_clusters)
    call_entries = cluster_entry_points(call_clusters, call_edges)
    cuts = suggest_cuts(call_clusters, call_edges, target)
    unused = unused_module_names(report)
    usage = package_usage(package_dir, unused) if package_dir is not None else {}
    return {
        "module": str(report.path),
        "module_name": report.module_name,
        "total_lines": report.total_lines,
        "target_lines": target,
        "classes": {
            cls: {
                "methods": names,
                "lines": sum(m.lines for m in report.methods if m.cls == cls),
            }
            for cls, names in report.classes.items()
        },
        "methods": [
            {
                "class": m.cls,
                "name": m.name,
                "lineno": m.lineno,
                "end_lineno": m.end_lineno,
                "lines": m.lines,
                "complexity": m.complexity,
                "attrs_read": sorted(m.attrs_read),
                "attrs_written": sorted(m.attrs_written),
                "calls_self": sorted(m.calls_self),
                "module_names": sorted(m.module_names),
            }
            for m in report.methods
        ],
        "clusters": [
            {
                "index": ci,
                "methods": [m.qualname for m in cluster],
                "lines": sum(m.lines for m in cluster),
                "entry_points": sorted(entries.get(ci, ())),
                "shared_state": sorted(state.get(ci, ())),
            }
            for ci, cluster in enumerate(clusters)
        ],
        "cross_cluster_edges": [{"from": src, "to": dst, "caller": caller} for src, dst, caller in edges],
        "call_clusters": [
            {
                "index": ci,
                "methods": [m.qualname for m in cluster],
                "lines": sum(m.lines for m in cluster),
                "entry_points": sorted(call_entries.get(ci, ())),
                "shared_state": sorted({a for m in cluster for a in m.attrs_read | m.attrs_written}),
            }
            for ci, cluster in enumerate(call_clusters)
        ],
        "call_cluster_edges": [{"from": src, "to": dst, "caller": caller} for src, dst, caller in call_edges],
        "suggested_cuts": cuts,
        "best_cut": best_cut(cuts),
        "extraction_seeds": extraction_seeds(report.methods),
        "state_owners": state_owners(report.methods),
        "init_only_state": sorted(init_only_state(report.methods)),
        "long_methods": [
            {"method": m.qualname, "lines": m.lines, "lineno": m.lineno}
            for m in report.methods
            if m.lines > long_method
        ],
        "complexity": {m.qualname: m.complexity for m in report.methods},
        "unused_module_names": sorted(unused),
        "unused_module_name_users": {name: sorted(set(users) - {report.path.stem}) for name, users in usage.items()},
        "import_cycles": import_cycles(package_dir) if package_dir is not None else [],
    }


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module", help="path to the lower/ module to analyse")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument(
        "--target-lines",
        type=int,
        default=DEFAULT_TARGET_LINES,
        help=f"line target for the module (default {DEFAULT_TARGET_LINES})",
    )
    parser.add_argument(
        "--long-method",
        type=int,
        default=DEFAULT_LONG_METHOD,
        help=f"threshold for the long-method report (default {DEFAULT_LONG_METHOD})",
    )
    parser.add_argument(
        "--package",
        default=None,
        help="package dir for the import-cycle report (default: the module's own dir)",
    )
    args = parser.parse_args(argv)

    path = Path(args.module)
    if not path.is_file():
        print(f"no such module: {path}", file=sys.stderr)
        return 2
    report = analyse_module(path)
    package_dir = Path(args.package) if args.package else path.parent
    if args.json:
        print(json.dumps(to_json(report, package_dir, args.target_lines, args.long_method), indent=2))
    else:
        print(format_text(report, package_dir, args.target_lines, args.long_method))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
