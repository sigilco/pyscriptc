"""Boxed/island resolution and island source extraction, split verbatim from `lower.__init__`.

`BoxedResolveMixin` holds the boxed fallback planner: the pre-lowering probe
that routes definitions between native and island execution, the cascade and
demotion fixpoints, the island seed source assembly, and the shadow-slot key
of an island-owned global. Every body below is the code that lived in
`lower.__init__`, unchanged: the planning decisions, fences and diagnostics
are byte-identical.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.errors import span_of
from pyscriptc.ffi_manifest import is_handle_type
from pyscriptc.lower._core import Fence, _maybe_type_kind, _type_kind
from pyscriptc.lower._stmt import _always_returns


# boxed:begin
def _bigint_growth(fn: ast.FunctionDef) -> str | None:
    """The growing operation of an unbounded int accumulation in `fn`, or None.

    Issue #677: annotated `int` lowers as i64 (divergence 1), so native
    arithmetic aborts on overflow where CPython grows a big int. This check
    is deliberately conservative: any in-place update of the same name by
    a multiplying operator (`*=`, `<<=`, `**=`) or an assign of
    `name <op> anything` back to `name` (the loop-carry shape
    `r = r * i` / `r <<= 1`) flags the definition. A single `name ** k`
    assign with a literal exponent of at most 62 and no loop in the body
    can never leave i64, so it is excluded. Add and subtract are not
    growth: two i64 adds of in-range values stay in range unless the
    operands were already huge, while `*`/`<<`/`**` double the bit length
    every step and outgrow i64 from small constants. The false-positive
    cost is one demotion to the island; the false-negative cost is an
    abort, so the check leans wide.
    """
    has_loop = any(isinstance(node, (ast.For, ast.While, ast.AsyncFor)) for node in ast.walk(fn))
    for node in ast.walk(fn):
        if isinstance(node, ast.AugAssign) and isinstance(node.op, (ast.Mult, ast.LShift, ast.Pow)):
            if isinstance(node.target, ast.Name):
                return f"'{node.target.id} {type(node.op).__name__}='"
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.BinOp)
            and isinstance(node.value.op, (ast.Mult, ast.LShift, ast.Pow))
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value.left, ast.Name)
            and node.value.left.id == node.targets[0].id
            and not (isinstance(node.value.op, ast.Pow) and not has_loop and _bounded_pow(node.value.right))
        ):
            return f"'{node.targets[0].id} {type(node.value.op).__name__}='"
    return None


def _bounded_pow(node: ast.expr) -> bool:
    """True for `**` with a literal exponent small enough to stay in i64."""

    return isinstance(node, ast.Constant) and isinstance(node.value, int) and 0 <= node.value <= 62


# boxed:end


class BoxedResolveMixin:
    # boxed:begin
    def _boxed_resolve(self, fences: list[Fence]) -> None:
        """The boxed planner, before any body lowers (issue #612).

        Runs the fence-free probe per definition so the plane map is known
        before call sites lower: a fenced definition's fences carry
        `_boxed_probe` and leave no trace, and its name lands in
        `island_fns`, which routes the calls to it through the island
        trampoline. Definitions the probe clears lower native with their
        real fences intact (a later fence in the body still refuses).
        """
        if not self.fallback_definitions:
            return
        saved_caught = self.caught
        try:
            for fn in self._boxed_functions:
                if fn.name in self.declared or fn.name in self.generic_fns:
                    continue
                # boxed:begin
                # Issue #676: the deferred decorator pass already planned a
                # name the island seed owns; its decorator fences dropped
                # with the definition, so it never lowers and no probe runs.
                if fn.name in self._boxed_island:
                    continue
                # boxed:end
                probe_fences: list[Fence] = []
                saved_function = self.function
                self.function = fn.name
                try:
                    self._boxed_probe(fn, probe_fences)
                finally:
                    self.function = saved_function
                if probe_fences:
                    self._boxed_island.add(fn.name)
        finally:
            self.caught = saved_caught
        self.island_fns = set(self._boxed_island)
        self._boxed_demoted_by = {}

        # Issue #677: annotated `int` is i64 (divergence 1), so a definition
        # that accumulates an unbounded int natively aborts on overflow where
        # the island would print CPython's big int. A conservative static
        # check finds the growth shape and routes the whole definition to the
        # island instead: an unannotated definition rides the island exactly
        # like a fenced one, so this closes the gap without changing the
        # typed-subset contract. Runs after the reset above so the demotion
        # reasons survive the #620 cascade below.
        for fn in self._boxed_functions:
            if fn.name in self.island_fns or fn.name not in self.sigs or fn.name in self.generic_fns:
                continue
            growth = _bigint_growth(fn)
            if growth is not None:
                self._boxed_island.add(fn.name)
                self._boxed_demoted_by[fn.name] = f"possible i64 overflow at {growth}"
        self.island_fns = set(self._boxed_island)
        boxed_fns_by_name = {fn.name: fn for fn in self._boxed_functions}

        # Issue #620: Cascade island definitions to native siblings.
        # If an island definition calls a native sibling, demote that sibling
        # transitively to a fixpoint.
        # Issue #674: a native caller passing a mutable container (list,
        # set, dict) to an island callee loses in-place mutation: the
        # trampoline marshals a fresh island copy and nothing copies the
        # cells back. Demote the caller to the island so the call is an
        # island-to-island call on the same object. One fixpoint: demoting
        # a caller (#674) can make its own native callees need the #620
        # demotion and the other way around.
        changed = True
        while changed:
            changed = False
            for fn in self._boxed_functions:
                if fn.name not in self.island_fns:
                    continue
                # Issue #675: a native sibling read as a value (passed as a
                # callable, reduced over, stored) is no more reachable
                # native-side than one called directly (issue #620): the
                # name does not resolve in the island namespace either way.
                # Demote any bare name load, not only a call.
                loaded = set()
                for node in ast.walk(fn):
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                        loaded.add(node.id)
                    elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                        # `mod.helper` names `mod`, not `helper`.
                        loaded.add(node.value.id)
                for callee in loaded:
                    if callee in boxed_fns_by_name and callee not in self.island_fns:
                        if callee in self.generic_fns:
                            continue
                        self.island_fns.add(callee)
                        self._boxed_island.add(callee)
                        if callee not in self._boxed_demoted_by:
                            self._boxed_demoted_by[callee] = fn.name
                        changed = True
            for fn in self._boxed_functions:
                if fn.name in self.island_fns:
                    continue
                if fn.name in self.generic_fns or fn.name in self.declared:
                    continue
                for node in ast.walk(fn):
                    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                        continue
                    callee = node.func.id
                    if callee not in self.island_fns or callee not in boxed_fns_by_name:
                        continue
                    if self._island_call_passes_mutable(callee):
                        self.island_fns.add(fn.name)
                        self._boxed_island.add(fn.name)
                        self._boxed_demoted_by.setdefault(
                            fn.name,
                            f"island function {callee} takes a mutable container argument "
                            "(a copy-in copy-out seam would lose in-place mutation, issue #674)",
                        )
                        changed = True
                        break

        # Issue #628: cascade the island class to the compiled functions
        # that name it. The class object and its instances exist only
        # island-side, so a function that uses one runs island-side too
        # (the #620 rule, with the class as the island definition).
        if self.island_classes:
            for fn in self._boxed_functions:
                if fn.name in self.island_fns:
                    continue
                used = sorted(
                    node.id for node in ast.walk(fn) if isinstance(node, ast.Name) and node.id in self.island_classes
                )
                if used:
                    self.island_fns.add(fn.name)
                    self._boxed_island.add(fn.name)
                    self._boxed_demoted_by.setdefault(fn.name, f"island class {used[0]}")

        self._boxed_sibling_fences: list[Fence] = []
        # Keep the PS2002 diagnostic only where demotion is impossible and say why.
        for fn in self._boxed_functions:
            if fn.name not in self.island_fns:
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    callee = node.func.id
                    if callee in self.sigs and callee not in self.island_fns:
                        if callee in self.generic_fns:
                            reason = (
                                f"generic compiled function {callee} cannot be demoted to the island; "
                                "the island-to-compiled seam is the `--emit binding` surface"
                            )
                        elif callee not in boxed_fns_by_name:
                            reason = (
                                f"{callee} is not a module-level definition and cannot be demoted to the island; "
                                "the island-to-compiled seam is the `--emit binding` surface"
                            )
                        else:
                            reason = (
                                "the island-to-compiled seam is the `--emit binding` surface; "
                                f"annotate {callee} so it lowers native-to-native, "
                                "or drop its annotations so it joins the island"
                            )
                        msg = f"{fn.name} runs on the island and cannot call the compiled function {callee}: {reason}"
                        fences.append(Fence("PS2002", msg, span_of(node, self.filename), fn.name))
                        self._boxed_sibling_fences.append(fences[-1])

    def _island_call_passes_mutable(self, callee: str) -> bool:
        """Whether the island callee has a mutable container parameter
        (issue #674).

        The callee's proven signature is the fact: an unprovable signature
        fenced the callee itself, so `sigs` answers for every parameter that
        marshals at all. A list (a set lowers as a list with the set flag),
        a dict or an optional wrapper of either mutates in place, so the
        copy-in seam would lose the mutation. Scalars, str and bytes are
        immutable and stay on the copy-in trampoline. Call-site binding is
        deliberately ignored: the callee's contract is that the parameter
        can be mutated, and any caller that binds it has the same hazard.
        """
        try:
            params, _ret = self.sigs[callee]
        except KeyError:
            return True
        for param in params:
            kind = _type_kind(param["type"])
            elem = param["type"].get("elem") or {}
            if kind in {"list", "dict"} or param["type"].get("set"):
                return True
            if param["type"].get("optional") and _maybe_type_kind(elem) in {"list", "dict"}:
                return True
        return False

    def _boxed_island_class(self, node: ast.ClassDef) -> None:
        """Plan one class definition for the island (issue #628).

        The class leaves every native table, so a compiled call site resolves
        it through the island handle path (or demotes, see `_boxed_resolve`),
        and the class lowering pass skips it: CPython defines it island-side.
        """
        self.island_classes.add(node.name)
        self.classes.pop(node.name, None)
        self.declared.discard(node.name)
        self.known_classes.discard(node.name)
        self.class_aliases.pop(node.name, None)

    def _boxed_replan(self, fences: list[Fence]) -> None:
        """Re-run the function planner after a class joins the island late.

        A class that only fences while its method bodies lower becomes
        island after `_boxed_resolve` already ran. `island_fns` recomputes
        from the accumulated `_boxed_island` set (so a demotion is never
        undone), and the sibling refusal fences the previous pass appended
        are dropped before the new pass appends its own.
        """
        if not self.fallback_definitions:
            return
        stale = set(map(id, self._boxed_sibling_fences))
        fences[:] = [fence for fence in fences if id(fence) not in stale]
        self._boxed_resolve(fences)
        # The entry's probe ran before this class joined the island, so it
        # may have cleared uses the island class now expresses differently.
        # A fence there is the whole top level fencing (Decision A): the
        # module body runs island-side, exactly as if the probe had run after
        # the class decision.
        entry_name = self.entry_fn_name
        if self._boxed_top_island or entry_name is None:
            return
        entry = next((fn for fn in self._boxed_functions if fn.name == entry_name), None)
        if entry is None:
            return
        probe: list[Fence] = []
        saved_function = self.function
        self.function = entry.name
        try:
            self._boxed_probe(entry, probe)
        finally:
            self.function = saved_function
        if probe:
            self._boxed_top_island = True
            self._boxed_island.add(entry.name)
            self.island_fns.add(entry.name)

    def _boxed_probe(self, fn: ast.FunctionDef, sink: list[Fence]) -> None:
        """Lower `fn`'s body into `sink`, discarding the IR.

        The scope setup mirrors the real body pass (issue #612): the
        signature's parameters become the locals, the per-function state
        starts fresh, and everything restores after. Without it the probe
        would fence on the function's own parameters (`url` is not a local)
        and misplan every function that reads one.
        """

        self.caught = sink
        try:
            saved: dict[str, Any] = {
                "locals": self.locals,
                "prelude": self.prelude,
                "temp": self.temp_n,
                "modules": self.island_modules,
                "class_handles": self.island_class_handles,
                "globals": self.fn_globals,
                "nonlocals": self.fn_nonlocals,
                "captures": self.captures,
                "frozen": self.frozen_names,
                "unwrap": self.opt_unwrap,
                "boxed": self.boxed_names,
                "fn_body": self.fn_body,
                "handle_locals": self.handle_locals,
                "consumed": self.consumed_handles,
                "returned": self.returned_handles,
                "borrowed": self.borrowed_handles,
            }
            try:
                self.temp_n = 0
                self.prelude = []
                self.island_modules = {}
                self.island_class_handles = {}
                self.fn_globals = self._globals_for(fn.body)
                self.fn_nonlocals = set()
                self.captures = {}
                params, ret = self._coro_body_sig(fn.name)
                self.locals = {param["name"]: param["type"] for param in params}
                self.frozen_names = set()
                self.opt_unwrap = {}
                self.boxed_names = set()
                self.fn_body = list(fn.body)
                self.handle_locals = {}
                self.consumed_handles = set()
                self.returned_handles = set()
                self.borrowed_handles = {
                    name for name, typ in self.locals.items() if isinstance(typ, dict) and is_handle_type(typ)
                }
                body = self._body(list(fn.body))
                if not self.caught:
                    body = self._scope_exit_frees(body, span_of(fn, self.filename))
                    if _type_kind(ret) != "none" and not _always_returns(body):
                        sink.append(
                            Fence(
                                "PS1004",
                                f"{fn.name} can fall off the end and return None",
                                span_of(fn, self.filename),
                                fn.name,
                            )
                        )
            except Fence as exc:
                sink.append(exc)
            finally:
                self.locals = saved["locals"]
                self.prelude = saved["prelude"]
                self.temp_n = saved["temp"]
                self.island_modules = saved["modules"]
                self.island_class_handles = saved["class_handles"]
                self.fn_globals = saved["globals"]
                self.fn_nonlocals = saved["nonlocals"]
                self.captures = saved["captures"]
                self.frozen_names = saved["frozen"]
                self.opt_unwrap = saved["unwrap"]
                self.boxed_names = saved["boxed"]
                self.fn_body = saved["fn_body"]
                self.handle_locals = saved["handle_locals"]
                self.consumed_handles = saved["consumed"]
                self.returned_handles = saved["returned"]
                self.borrowed_handles = saved["borrowed"]
        finally:
            self.caught = []

    def _boxed_island_source(
        self,
        tree: ast.Module,
        island: set[str],
        top_level_island: bool,
        globals_out: dict[str, str],
    ) -> str:
        """The source text the island seed execs (issue #612, #613).

        With a native top level (Stage 2) the seed must not re-execute
        side-effecting top-level statements (the native init already ran
        them; a second run doubles output). It carries the statements an
        island function needs to resolve its names: every top-level `import`
        (module bindings, no side effects beyond the import itself, which
        CPython caches), module assignments whose value is a literal or a
        deterministic initializer, and the source segment of every island
        definition.

        With the island top level (Stage 3, Decision A) the seed is the
        module's own source text: the embedded interpreter execs it as the
        `__main__` body exactly once (`ps_island_top_seed` sets `__name__`),
        so the script's main guard fires island-side and `raise
        SystemExit(...)` delivers the process status (Decision E). The
        annotated scalar globals the top level binds are recorded in
        `globals_out` so the emitter unboxes them once at init into native
        readers (Decision E).
        """

        if not top_level_island:
            return self._boxed_stage2_source(tree, island)
        # The annotated scalar globals the top level binds join the native
        # slot table from their module-level AnnAssign, so a typed native
        # reader unboxes the island value once at init. An annotated global
        # normally binds as a constant (no slot); here the annotation gives
        # the slot its type instead. An unannotated top-level global never
        # got a slot (it fenced): it stays island-only.
        for stmt in tree.body:
            if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                continue
            name = stmt.target.id
            if name in self.constants:
                # The split bound it as a module constant (annotation plus a
                # simple value). The island top level owns the binding, so
                # the constant replay leaves and the slot table takes the
                # name: a typed native reader unboxes the island value
                # through the shadow slot instead of folding the literal.
                del self.constants[name]
                self.declared.discard(name)
            if name in self.declared:
                continue
            slot = self.global_slots.get(name)
            if slot is None:
                try:
                    typ = self._annotation(stmt.annotation)
                except Fence:
                    continue
                if not self._boxed_unboxable_global(typ):
                    continue
                self.global_slots[name] = {"type": typ, "active": False}
                slot = self.global_slots[name]
            elif slot.get("island"):
                continue
            else:
                typ = slot.get("type")
                if slot.get("active") or not self._boxed_unboxable_global(typ):
                    continue
            try:
                self._activate_slot(name, stmt)
            except Fence:
                self.global_slots.pop(name, None)
                self.declared.discard(name)
                continue
            slot["island"] = True
            kind = _type_kind(typ)
            if kind == "list":
                pad = self._boxed_island_pad_code(typ["elem"])
                globals_out[name] = f"5:{pad}"
            else:
                globals_out[name] = str({"int": 1, "str": 3, "bool": 4, "bytes": 6}[kind])
        for slot_name, slot in self.global_slots.items():
            if not slot.get("active") or slot.get("island"):
                continue
            typ = slot.get("type")
            if not self._boxed_unboxable_global(typ):
                continue
            slot["island"] = True
            kind = _type_kind(typ)
            if kind == "list":
                pad = self._boxed_island_pad_code(typ["elem"])
                globals_out[slot_name] = f"5:{pad}"
            else:
                globals_out[slot_name] = str({"int": 1, "str": 3, "bool": 4, "bytes": 6}[kind])
        return self.source

    def _boxed_island_pad_code(self, typ: dict[str, Any]) -> int:
        """The `pad` element code `_island_pad` emits, mirrored at the lowerer.

        The trampoline stores an unboxed list cell per element; the pad names
        the element kind the same way the emitter's `_ISLAND_ARG_CODES` does.
        """

        from pyscriptc.emit.llvm._abi import AbiMixin

        return AbiMixin._ISLAND_ARG_CODES[_type_kind(typ)]

    def _boxed_stage2_source(self, tree: ast.Module, island: set[str]) -> str:
        """The Stage 2 seed: imports, literal module values, island defs."""
        segments: list[str] = []
        for stmt in tree.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                segments.append(ast.get_source_segment(self.source, stmt) or "")
                continue
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and self._boxed_is_literal_module_value(stmt):
                segment = ast.get_source_segment(self.source, stmt)
                if segment:
                    segments.append(segment)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in island:
                segment = self._boxed_definition_source(node)
                if segment:
                    segments.append(segment)
            # Issue #628: an island class definition joins the seed too, in
            # source order beside the island functions that instantiate it.
            elif isinstance(node, ast.ClassDef) and node.name in self.island_classes:
                segment = self._boxed_definition_source(node)
                if segment:
                    segments.append(segment)
        return "\n\n\n".join(segment for segment in segments if segment)

    def _boxed_definition_source(self, node: ast.AST) -> str:
        """One definition's source text, decorators included.

        `ast.get_source_segment` starts a decorated definition at its `def`
        or `class` line, so the decorator lines come from the decorator
        expressions themselves. An island class without its decorator would
        silently lose it (a `@dataclass(frozen=True)` class would lose its
        generated `__init__`, issue #628).
        """
        segment = ast.get_source_segment(self.source, node)
        if not segment:
            return ""
        imprint = []
        for decorator in getattr(node, "decorator_list", []) or []:
            text = ast.get_source_segment(self.source, decorator)
            if text:
                imprint.append(f"@{text}")
        if not imprint:
            return segment
        return "\n".join([*imprint, segment])

    def _boxed_is_literal_module_value(self, stmt: ast.Assign | ast.AnnAssign) -> bool:
        """True when the value is safe to re-execute island-side.

        A literal (a constant or a display of constants) is always safe.
        An initializer built from deterministic calls (`Path.home() / x`)
        also re-executes to the same value, and the dogfood scripts read
        such globals (`SESSION_DIR`, `ROOT`) from island functions; the
        re-execution divergence is documented (plan Decision E: globals
        are one-directional at this seam, and Stage 3 replaces this rule
        with an island-owned top level). An import-bound name resolves
        through the seeded imports, so attribute and call chains over it
        lower island-side too.
        """
        value = getattr(stmt, "value", None)
        if value is None:
            return False
        for node in ast.walk(value):
            if isinstance(node, (ast.expr_context, ast.operator, ast.boolop, ast.unaryop, ast.cmpop)):
                continue
            if isinstance(node, ast.Constant):
                continue
            if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
                continue
            if isinstance(node, ast.UnaryOp):
                continue
            if isinstance(
                node,
                (
                    ast.Name,
                    ast.Attribute,
                    ast.Call,
                    ast.BinOp,
                    ast.BoolOp,
                    ast.JoinedStr,
                    ast.FormattedValue,
                    ast.IfExp,
                ),
            ):
                continue
            return False
        return True

    def _boxed_unboxable_global(self, typ: Any) -> bool:
        """True when an island global's annotation is a marshalable scalar.

        Decision E (issue #613): a native reader unboxes the island value
        once after the seed runs; the accepted shapes are the ones the
        native global-slot runtime can hold and the native readers can read
        back - bool, int, str, bytes, and lists of scalars. Float has no
        native slot getter today, so it stays island-side (a native reader
        of it fences with the named diagnostic); `Path` and other handles
        stay island-side the same way.
        """

        if not isinstance(typ, dict):
            return False
        if typ.get("island") or typ.get("optional"):
            return False
        kind = _maybe_type_kind(typ)
        if kind in {"bool", "int", "str", "bytes"}:
            return True
        if kind == "list":
            elem = typ.get("elem") or {}
            return not elem.get("optional") and _maybe_type_kind(elem) in {"bool", "int", "float", "str", "bytes"}
        return False

    def _boxed_cross_module_island(self) -> bool:
        """Whether a call may cross into another module's island floor.

        The plane split (the per-module island granularity) is on when the
        compile runs the fallback tier and the program keeps both planes.
        The merge sets the flag once it knows the program is not one whole
        island.
        """

        return self._boxed_plane_split

    # boxed:end

    # boxed:begin
    def _slot_island_key(self, name: str) -> str:
        """The shadow slot key of an island-owned global (issue #613)."""

        return f"__island__{self._slot_key(name)}"

    # boxed:end
