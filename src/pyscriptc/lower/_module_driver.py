"""The module driver, split verbatim from `lower.__init__`.

`ModuleDriverMixin` holds `Lowerer.module`, the root pass that lowers a whole
module: the depth guard, the entry split, the class and function collection
passes, the boxed plane decision and the module record. `Lowerer` composes it,
so the driver stays reachable as `self.module(tree)`. Every body below is the
code that lived in `lower.__init__`, unchanged.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.errors import span_of
from pyscriptc.ffi_manifest import is_handle_type
from pyscriptc.ir import IR_VERSION, SUBSET, cpython_version
from pyscriptc.lower._core import Fence, _has_typevar, _type_kind
from pyscriptc.lower._coro_adapter import coro_module
from pyscriptc.lower._stmt import _always_returns

# Issue #587: the deepest AST nesting the recursive lowering passes handle.
# Measured against the interpreter's own recursion limit: a chain around 250
# levels already grazes it, so the fence fires well before the crash while
# leaving every realistic program native. CPython 3.14's parser only bounds
# parenthesis nesting, not attribute or operator chains, so a source that
# parses can still be too deep to lower.
MAX_LOWER_DEPTH = 200


def _ast_depth(tree: ast.AST) -> int:
    """The maximum AST depth (edges from the root), iteratively.

    Iterative on purpose: `ast.walk`-style recursion on a pathological tree
    is exactly what this guard exists to fence.
    """
    deepest = 0
    stack: list[tuple[ast.AST, int]] = [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > deepest:
            deepest = depth
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return deepest


def _deepest_node(tree: ast.AST) -> ast.AST:
    """The first node at the maximum depth: the fence span points there."""
    best: ast.AST = tree
    best_depth = 0
    stack: list[tuple[ast.AST, int]] = [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > best_depth:
            best, best_depth = node, depth
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return best


class ModuleDriverMixin:
    def module(self, tree: ast.Module) -> dict[str, Any]:
        # Issue #587: a deeply nested AST overflows the lowerer's recursive
        # descent with an uncaught RecursionError. CPython's parser still
        # accepts these programs (its own nesting guard only counts
        # parentheses), so the guard lives here: past the depth the
        # recursive passes can handle, the module fences with the fit
        # diagnostic instead of crashing the compiler.
        if (depth := _ast_depth(tree)) > MAX_LOWER_DEPTH:
            fence = Fence(
                "PS3002",
                f"nesting is deeper than the lowerer handles: {depth} AST levels (over {MAX_LOWER_DEPTH})",
                span_of(_deepest_node(tree), self.filename),
                None,
            )
            return {
                "irVersion": IR_VERSION,
                "subset": SUBSET,
                "cpython": cpython_version(),
                "sourceFile": self.filename,
                "entry": None,
                "functions": [],
                "classes": [],
                "fences": [fence.as_dict()],
            }
        tree = self._desugar(tree)
        fences: list[Fence] = []
        functions: list[ast.FunctionDef] = []
        class_nodes: list[ast.ClassDef] = []
        live = self._live_statements(list(tree.body), fences)
        self.global_slots = self._scan_global_slots(live)
        entry_stmts = self._split_entry(live, functions, class_nodes, fences)
        # boxed:end
        entry_fn = self._entry_function(entry_stmts, functions)
        entry_name = entry_fn[0].name if entry_fn else None
        self.entry_fn_name = entry_name
        functions.extend(entry_fn)
        # boxed:end
        self._install_synthetics(class_nodes, fences)
        imported = {name for name, info in self.classes.items() if info.get("imported")}
        imported.update(self.class_aliases)
        self.known_classes = {node.name for node in class_nodes}
        self.known_classes.update(name for name, info in self.classes.items() if info.get("synthetic"))
        self.known_classes.update(imported)
        for node in class_nodes:
            self.function = node.name
            self.caught = []
            # boxed:begin
            # Issue #628: a class whose definition fences (a decorator, class
            # keywords, a class-body statement) is planned for the island, and
            # its fences drop with it: the island seed defines the class in
            # CPython, exactly like an island function.
            if self.fallback_definitions:
                class_fences: list[Fence] = []
                try:
                    self._collect_class(node)
                except Fence as exc:
                    class_fences.append(exc)
                class_fences.extend(self.caught)
                if class_fences:
                    self._boxed_island_class(node)
                else:
                    fences.extend(class_fences)
                continue
            # boxed:end
            try:
                self._collect_class(node)
            except Fence as exc:
                fences.append(exc)
            fences.extend(self.caught)
        for fn in functions:
            self.function = fn.name
            if fn.name in self.declared:
                fences.append(Fence("PS2002", f"{fn.name} is already bound", span_of(fn, self.filename), fn.name))
                continue
            try:
                kind = "static" if self._is_module_staticmethod(fn) else "method"
                self.sigs[fn.name] = self._coro_signature(fn) or self._signature(fn, None, kind)
            except Fence as exc:
                fences.append(exc)
                # boxed:begin
                # The signature itself fenced (an unannotated parameter or
                # return, issue #612): under the fallback tier this plans
                # the definition for the island, and the planner drops the
                # fence with the definition.
                if self.fallback_definitions:
                    self._boxed_island.add(fn.name)
                # boxed:end
            else:
                params, ret = self.sigs[fn.name]
                if _has_typevar(ret) or any(_has_typevar(param["type"]) for param in params):
                    self.generic_fns[fn.name] = fn
        self._pin_call_site_params(functions, fences)
        if self.deferred_module:
            self._bind_deferred_module(fences)
        # boxed:begin
        # The boxed plane decision happens here, before any body lowers, so
        # every call site sees the same `island_fns` (issue #612). Names the
        # deferred decorator pass planned early (issue #676) join the probe
        # set, so `_boxed_resolve` sees the same island set the planner
        # would have produced from a body fence.
        self._boxed_functions = list(functions)
        self._boxed_resolve(fences)
        # The island top level (issue #613, Decision A): the synthesized
        # entry is the module body, so it is probed like any definition.
        # A fenced probe means module-level statements fence native - the
        # whole top level runs island-side and the entry's fences drop with
        # it (the entry never lowers native: the body pass skips it).
        # Module-level import fences (PS2001/PS2002, issue #627) route the
        # module top level to the island floor as well. A deferred module
        # assignment fence ("a module assignment must be a constant", Refs
        # #641) carries the `__module__` sentinel instead of None, and
        # routes the top level the same way: the seed execs the real
        # statement, so the value is CPython's own.
        if self.fallback_definitions:
            probe_fences: list[Fence] = []
            if entry_fn:
                saved_function = self.function
                self.function = entry_name
                try:
                    self._boxed_probe(entry_fn[0], probe_fences)
                finally:
                    self.function = saved_function
            has_import_fence = any(
                fence.function in (None, "__module__") and fence.code in ("PS2001", "PS2002") for fence in fences
            )
            self._boxed_top_island = bool(probe_fences) or has_import_fence
            if self._boxed_top_island:
                if probe_fences:
                    fences.extend(probe_fences)
                if entry_name:
                    self._boxed_island.add(entry_name)
        # boxed:end
        if self.cataloguing:
            self.function = None
            return {
                "irVersion": IR_VERSION,
                "subset": SUBSET,
                "cpython": cpython_version(),
                "sourceFile": self.filename,
                "entry": None,
                "functions": [],
                "classes": [],
                "fences": [fence.as_dict() for fence in fences],
            }
        lowered_classes: list[dict[str, Any]] = []
        for node in class_nodes:
            info = self.classes.get(node.name)
            if info is None or info.get("synthetic") or info.get("typevars"):
                continue
            self.function = node.name
            self.caught = []
            # boxed:begin
            # Issue #628: a class that only fences while its method bodies
            # lower joins the island too. The fence and the class's fences
            # drop, and the function planes are re-planned so a compiled
            # caller of the now-island class demotes with it.
            class_mark = len(fences)
            nested_mark = len(self.nested_fns)
            # boxed:end
            try:
                lowered = self._lower_class(node)
            except Fence as exc:
                fences.append(exc)
                fences.extend(self.caught)
                # boxed:begin
                if self.fallback_definitions:
                    self._boxed_island_class(node)
                    del fences[class_mark:]
                    self.nested_fns = self.nested_fns[:nested_mark]
                    self._boxed_replan(fences)
                # boxed:end
                continue
            fences.extend(self.caught)
            # boxed:begin
            if self.fallback_definitions and self.caught:
                self._boxed_island_class(node)
                del fences[class_mark:]
                self.nested_fns = self.nested_fns[:nested_mark]
                self._boxed_replan(fences)
                continue
            # boxed:end
            if lowered is not None and not self.caught:
                lowered_classes.append(lowered)
        lowered_classes.extend(self._synthetic_classes({item["name"] for item in lowered_classes}))
        lowered: list[dict[str, Any]] = []
        for fn in functions:
            if fn.name not in self.sigs or fn.name in self.generic_fns:
                continue
            # boxed:begin
            # A definition planned for the island never lowers (issue
            # #612): its probe already fenced, the fences dropped, and the
            # island runs its real body. The entry joins this list when the
            # top level is island (issue #613).
            if self.fallback_definitions and fn.name in self.island_fns:
                continue
            # boxed:end
            self.function = fn.name
            self.temp_n = 0
            self.prelude = []
            # boxed:begin
            self.island_modules = {}
            self.island_class_handles = {}
            # boxed:end
            self.caught = []
            self.fn_globals = self._globals_for(fn.body)
            self.fn_nonlocals = set()
            self.captures = {}
            params, ret = self._coro_body_sig(fn.name)
            self.locals = {param["name"]: param["type"] for param in params}
            self.frozen_names = set()
            self.opt_unwrap = {}
            self.boxed_names = set()
            self.fn_body = list(fn.body)
            # Manifest handle ownership is per-function scope: a fresh
            # function owns nothing (issue #224). Its handle parameters are
            # borrows: the caller keeps ownership.
            self.handle_locals = {}
            self.consumed_handles = set()
            self.returned_handles = set()
            self.borrowed_handles = {
                name for name, typ in self.locals.items() if isinstance(typ, dict) and is_handle_type(typ)
            }
            # Function-local imports rebind module names: snapshot the
            # compile-time binding tables so they restore when the body ends.
            saved_bindings = dict(self.bindings)
            saved_aliases = dict(self.type_aliases)
            saved_declared = set(self.declared)
            saved_typevars = dict(self.typevars)
            try:
                body = self._scope_exit_frees(self._body(list(fn.body)), span_of(fn, self.filename))
                if self.caught:
                    fences.extend(self.caught)
                    # The entry is not a user function: its fences are already
                    # in the module list and block emission, so the partial
                    # body still carries the flip signal in the IR.
                    if entry_name is None or fn.name != entry_name:
                        continue
                if _type_kind(ret) != "none" and not _always_returns(body):
                    self.fail(fn, "PS1004", f"{fn.name} can fall off the end and return None")
                record = {
                    "name": self._ir_name(fn.name),
                    "params": params,
                    "returnType": ret,
                    "locals": [{"name": name, "type": typ} for name, typ in self.locals.items()],
                    "body": body,
                    "span": span_of(fn, self.filename),
                }
                if self.boxed_names:
                    record["boxed"] = sorted(self.boxed_names)
                lowered.append(record)
            except Fence as exc:
                fences.append(exc)
            finally:
                self.bindings = saved_bindings
                self.type_aliases = saved_aliases
                self.declared = saved_declared
                self.typevars = saved_typevars
        self._drain_instances(lowered, lowered_classes, fences)
        lowered.extend(self.nested_fns)
        lowered_classes.extend(self._synthetic_classes({item["name"] for item in lowered_classes}))
        # The deferred classes (the argparse namespace) grew their fields
        # while the statements lowered, so they close here.
        lowered_classes.extend(self._synthetic_classes({item["name"] for item in lowered_classes}, deferred_only=True))
        # boxed:begin
        # The boxed per-definition planner (issue #612): the fence list of a
        # definition planned for the island leaves the module fence list and
        # the definition leaves the lowered list (its body never lowered).
        # Stage 3 (issue #613): a fenced entry probe plans the whole top
        # level for the island (Decision A is all-or-nothing in both
        # directions), and the annotated scalar globals the top level binds
        # unbox once into native readers.
        fallback_map: dict[str, Any] | None = None
        if self.fallback_definitions:
            from pyscriptc import boxed as _boxed

            island = set(self.island_fns)
            # The island top level (issue #613): the entry name is the
            # island's own entry (the seed runs the whole module body), so
            # it stays out of the native lowered list and its fences drop
            # with the top level. A native top level keeps the Stage 2
            # shape: the entry lowers native, an island function is a
            # trampoline call from it.
            if self._boxed_top_island:
                if entry_name:
                    island.add(entry_name)
                    lowered = [record for record in lowered if record["name"] != entry_name]
                fences = [
                    fence
                    for fence in fences
                    if (fence.function != entry_name if entry_name is not None else True)
                    and not (fence.function in (None, "__module__") and fence.code in ("PS2001", "PS2002"))
                ]
            else:
                island.discard(entry_name)
            self.island_fns = island
            lowered = [record for record in lowered if record["name"] not in island]
            siblings = set(map(id, self._boxed_sibling_fences))
            fences = [fence for fence in fences if fence.function not in island or id(fence) in siblings]
            # The island seed is the whole module body island-side (issue
            # #613, the original source, `__name__ = "__main__"`) or the
            # Stage 2 shape (only the pieces island functions need resolve
            # their names).
            top_level_island = self._boxed_top_island
            all_names = [record["name"] for record in lowered] + [fn.name for fn in functions]
            fallback_map = _boxed.plane_map(
                [fence.as_dict() for fence in fences],
                [{"name": n} for n in dict.fromkeys(all_names)],
                entry_name,
                top_level_island,
                demoted_by=self._boxed_demoted_by,
                # Issue #628: the class planes the collect and lowering passes
                # decided, so the summary reports what the binary does.
                classes=[{"name": node.name, "island": node.name in self.island_classes} for node in class_nodes],
            )
            for name in island:
                if name == entry_name or (name.startswith("__") and name.endswith("__")):
                    continue
                fallback_map["functions"][name] = _boxed.ISLAND
            for name in self.island_classes:
                fallback_map["classes"][name] = _boxed.ISLAND
            island_globals: dict[str, str] = {}
            module_plane = ""
            # An island class needs the seed even when no function rides it:
            # a native class method reaches the class through its handle.
            if island or top_level_island or self.island_classes:
                module_plane = self._boxed_island_source(tree, island, top_level_island, island_globals)
        # boxed:end
        self.function = None
        names = [item["name"] for item in lowered]
        module = {
            "irVersion": IR_VERSION,
            "subset": SUBSET,
            "cpython": cpython_version(),
            "sourceFile": self.filename,
            # A definition-only module keeps the old shape: entry is the first
            # function. Only a synthesized entry overrides that.
            "entry": entry_name if entry_name in names else lowered[0]["name"] if lowered else None,
            "functions": lowered,
            "classes": lowered_classes,
            "fences": [fence.as_dict() for fence in fences],
        }
        # boxed:begin
        if fallback_map is not None:
            module["fallback"] = fallback_map
            if module_plane:
                module["islandSource"] = module_plane
            if island_globals:
                module["islandGlobals"] = island_globals
        # boxed:end
        if self.global_inits:
            module["globals"] = self.global_inits
        return self._coro_finish(coro_module(module))
