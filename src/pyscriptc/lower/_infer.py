"""Type inference and pinning, split verbatim from `lower.__init__`.

`TypeInferMixin` holds the inference peek, signature inference, call-site
type collection and the pin/bridge family; `Lowerer` composes it, so every
method stays reachable as `self._infer_type_of(...)` and friends. Every body
below is the code that lived in `lower.__init__`, unchanged: the IR and
diagnostics are byte-identical.
"""

from __future__ import annotations

import ast
from typing import Any, NoReturn

from pyscriptc.ir import types_equal
from pyscriptc.lower._core import (
    Fence,
    _has_typevar,
    _ir_kinds_of,
    _maybe_type_kind,
    _peek_annotated_params,
    _peek_annotation,
    _type_kind,
    _type_rt,
)


class TypeInferMixin:
    def _record_inference(self, record: dict[str, Any]) -> None:
        """Record one accepted inference fact.

        The list lives on the bridge when one is wired (a package compile
        gathers every file's records in one place) and on the lowerer
        otherwise (a single-file compile with no bridge).
        """
        target = self.bridge
        sink = getattr(target, "infer_records", None)
        if sink is None:
            self.infer_records.append(record)
        else:
            sink.append(record)

    def _bridge_type(self, node: ast.expr) -> dict[str, Any] | None:
        """The IR type the checker inferred for an expression, or None.

        The bridge answers only when it proved a type the lowered subset can
        express; anything else returns None and the caller keeps its fence.
        """
        if self.bridge is None:
            return None
        inferred = self.bridge.inferred_type(node, self.classes)
        if inferred is None:
            return None
        return inferred

    def _bridge_def(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> Any:
        """DefFacts the checker holds for one `def`, or None without a bridge."""
        if self.bridge is None:
            return None
        return self.bridge.def_facts(node)

    def _bridge_param_type(self, fn: ast.FunctionDef | ast.AsyncFunctionDef, arg: ast.arg) -> dict[str, Any] | None:
        """The IR type the checker proved for an unannotated parameter, or None."""
        if self.bridge is None:
            return None
        return self.bridge.param_type(fn, arg.arg, self.classes)

    def _checker_pin_type(self, fn: ast.FunctionDef | ast.AsyncFunctionDef, arg: ast.arg) -> dict[str, Any] | None:
        """The IR type the package's call sites proved for this parameter, or None.

        The pin is a whole-package fact (`pyscriptc infer` ran the checker
        over every module and reduced the call sites to the parameters
        every caller agreed on). It applies only to a module-level def the
        pin names by its qualified name; a method or a def the pin does not
        name keeps its fence.
        """
        if self.call_site_facts is None:
            return None
        from pyscriptc.inferfacts import parse_pin_type

        pins = self.call_site_facts.pins_for(f"{self.module_name}.{fn.name}" if self.module_name else fn.name)
        pin = pins.get(arg.arg)
        if pin is None:
            return None
        from pyscriptc.factsbridge import facts_to_ir

        parsed = parse_pin_type(pin.kind)
        if parsed is None:
            return None
        ir = facts_to_ir(parsed, self.classes)
        if ir is not None:
            self._record_inference(
                {
                    "kind": "param",
                    "module": self.module_name,
                    "name": f"{fn.name}.{arg.arg}",
                    "type": pin.kind,
                    "proof": (
                        f"pyrefly proved the type of every argument passed to {arg.arg} across {pin.sites} call site(s)"
                    ),
                }
            )
        return ir

    def _bridge_return_type(self, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any] | None:
        """The IR type the checker inferred for a missing return annotation."""
        if self.bridge is None:
            return None
        return self.bridge.return_type(fn, self.classes)

    _PEEK_UNSAFE = {"abi_call", "free", "consume", "yield_expr", "await_expr"}

    def _infer_type_of(self, node: ast.expr) -> dict[str, Any] | None:
        """The type lowering would give `node`, or None when it does not lower.

        The peek (issue #500) lowers the expression speculatively: the IR
        goes to a throwaway prelude and the temp counter is restored, so a
        non-trivial value lowers twice. Comprehension scaffolding is nested
        and restored like any other state; the read-only expressions return
        identical types on the real pass, which then emits the effects.
        Calls whose results are lowered outside the IR (abi thunks, handle
        frees and consumes) are refused: the peek must not run the runtime
        twice. A fence inside the peek is the refusal, not a diagnostic:
        inference never guesses, and the caller keeps its own message.
        """

        class _Peek:
            def __enter__(s) -> bool:
                if self.inf_peek or self.function is None:
                    return False
                s.saved_prelude = self.prelude
                s.saved_temp_n = self.temp_n
                s.saved_fences = self.caught
                s.saved_comp = (
                    self.comp_elt,
                    self.comp_key,
                    self.comp_kind,
                    self.comp_gens,
                    self.comp_ifs,
                    self.comp_dest,
                    self.comp_init,
                    self.comp_expect,
                    self.comp_ready,
                    self.comp_pred,
                )
                s.saved_dead = self.dead_names
                s.saved_exc = self.exc_stack
                s.saved_broke = self.broke_flag
                s.saved_break = self.break_flag
                s.saved_cont = self.continue_flag
                # boxed:begin
                # A peek cannot bind an island module handle local (issues
                # #650, #643): the island calls it walks return an isolated
                # handle node instead of touching the scope's prelude.
                s.saved_peeking = self.peeking
                self.peeking = True
                # boxed:end
                self.prelude = []
                self.caught = []
                self.inf_peek = True
                self.inf_depth += 1
                return True

            def __exit__(s, *exc) -> None:
                if not hasattr(s, "saved_prelude"):
                    return
                self.inf_depth -= 1
                if self.inf_depth == 0:
                    self.inf_peek = False
                self.prelude = s.saved_prelude
                self.temp_n = s.saved_temp_n
                self.caught = s.saved_fences
                (
                    self.comp_elt,
                    self.comp_key,
                    self.comp_kind,
                    self.comp_gens,
                    self.comp_ifs,
                    self.comp_dest,
                    self.comp_init,
                    self.comp_expect,
                    self.comp_ready,
                    self.comp_pred,
                ) = s.saved_comp
                self.dead_names = s.saved_dead
                self.exc_stack = s.saved_exc
                self.broke_flag = s.saved_broke
                self.break_flag = s.saved_break
                self.continue_flag = s.saved_cont
                # boxed:begin
                self.peeking = s.saved_peeking
                # boxed:end

        if any(k in self._PEEK_UNSAFE for k in _ir_kinds_of(node)):
            return None
        with _Peek() as ok:
            if not ok:
                return None
            try:
                value = self._expr(node)
            except Fence:
                return None
            if self.caught:
                return None
            return value["type"]

    def _infer_type_known(self, node: ast.expr) -> dict[str, Any] | None:
        """The type of a call to a `def` this module already signed, or None.

        The peek cannot lower a user call speculatively (the callee's body
        may not have lowered yet), but the signature pass has already
        proven the return types of every def that signed. A direct call
        whose callee is one of those names returns the signature's return
        type without lowering anything: the real pass emits the call.
        """

        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            return None
        # boxed:begin
        # The boxed tier (issue #612): a call to an island function is an
        # opaque handle, never a typed return; the peek must not give the
        # call site the signature's return type, because the body never
        # lowers and the trampoline marshals by annotation only.
        if node.func.id in self.island_fns:
            return None
        # boxed:end
        sig = self.sigs.get(node.func.id)
        if sig is None or node.func.id in self.generic_fns:
            return None
        params, ret = sig
        if _type_kind(ret) == "none" or _has_typevar(ret):
            return None
        if any(k in self._PEEK_UNSAFE for k in _ir_kinds_of(node)):
            return None
        return ret

    def _tuple_fields_of(self, typ: dict[str, Any] | None) -> list[dict[str, Any]] | None:
        """The field types of a synthetic tuple class type, or None.

        `_tuple_type` spells the fields into the class name
        (`tuple_3_str_str_str`); this decodes the slugs back to the IR
        types. A type that is not a synthetic tuple, or a slug the subset
        cannot express, returns None so the caller refuses rather than
        approximate.
        """

        if not isinstance(typ, dict) or _maybe_type_kind(typ) != "class":
            return None
        info = self.classes.get(typ["name"])
        if info is None or not info.get("tuple") or not info.get("synthetic"):
            return None
        return [field["type"] for field in info["fields"]]

    def _infer_signature(self, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any] | None:
        """The return type the function's own body proves, or None.

        The peek (issue #500) scans the body's `return` statements, lowers
        each returned expression under the peek, and keeps the type when
        every return agrees, at least one return exists, and every path
        returns. Only what the body proves is acceptable: a missing
        parameter annotation, a call that does not lower or a type the
        subset cannot express means the peek returns None and the caller
        keeps its fence. Defaults are irrelevant here on purpose: a typed
        default is a value at the call site, never the body's parameter
        type, so it does not flow into the inferred return. The PS1004
        fall-off-the-end fence is replayed by the real pass, which signs
        and lowers the body for real.

        When the body's own proof fails, the checker's proof is the
        fallback (`--bridge pyrefly`, `pyscriptc infer`): the checker
        types the whole body, so it sees returns the subset's peek cannot
        lower. The checker's answer is used only when the subset can
        express it, and it is recorded so `compile --debug` shows it.
        """
        if fn.returns is not None or self.function is None:
            return None
        if self.inf_peek:
            # A signature being peeked proves no further def's return: the
            # nested peek would replay the same fences and recurse.
            return None
        if isinstance(fn, ast.AsyncFunctionDef):
            return None
        for node in ast.walk(fn):
            if isinstance(node, (ast.Yield, ast.YieldFrom)):
                return None
        # The body's own local assignments participate: a returned value
        # may name a local (`return mapping.get(k)`), and the local's type
        # is the same body-proven fact this peek derives. The scan is the
        # same top-level shape the real body lowers; a value that does not
        # lower leaves the name out.
        saved_locals = self.locals
        body_locals = dict(_peek_annotated_params(self, fn))
        # The annotated parameters are visible to the scan immediately: a
        # value expression may name one (`[0.0] * n` under `n: int`), and
        # the fact is the def's own annotation, not a guess.
        self.locals = {**saved_locals, **body_locals}
        saved_fn_body = self.fn_body
        self.fn_body = list(fn.body)
        try:
            for item in fn.body:
                if isinstance(item, ast.Assign) and isinstance(item.targets[0], ast.Name):
                    name = item.targets[0].id
                    if name in body_locals:
                        continue
                    # An empty display says nothing on its own; the same
                    # accumulated-type pass the real assignment uses reads
                    # the appends and item writes in this body. The fn_body
                    # window above is what that pass scans.
                    typ = self._infer_accumulated_type(item) or self._infer_type_of(item.value)
                    if typ is not None and _type_kind(typ) != "none":
                        body_locals[name] = typ
                        self.locals[name] = typ
                elif (
                    isinstance(item, ast.Assign)
                    and len(item.targets) == 1
                    and isinstance(item.targets[0], ast.Tuple)
                    and all(isinstance(elt, ast.Name) for elt in item.targets[0].elts)
                ):
                    # `stem, dot, ext = value.rpartition(".")`: a fixed-arity
                    # tuple unpack of a call the peek can prove. The synthetic
                    # tuple class name carries the field kinds
                    # (`tuple_3_str_str_str`), and each name takes its field
                    # type; a type the name does not decode leaves the whole
                    # unpack out (never a guess).
                    typ = self._infer_type_of(item.value)
                    fields = self._tuple_fields_of(typ)
                    if fields is not None and len(fields) == len(item.targets[0].elts):
                        for elt, field in zip(item.targets[0].elts, fields, strict=True):
                            body_locals[elt.id] = field
                            self.locals[elt.id] = field
        finally:
            self.fn_body = saved_fn_body
        self.locals = {**saved_locals, **body_locals}
        returns: list[dict[str, Any]] = []
        # The return scan lowers returned values through `_infer_type_of`,
        # whose own peek guard refuses a nested peek. The scan window sets
        # no flag here: the peek opens per returned expression.
        try:
            for stmt in ast.walk(fn):
                if isinstance(stmt, ast.Return):
                    returns.append(stmt)
            if any(item.value is None for item in returns):
                # A bare `return`: the body proves nothing beyond None, but
                # mixing bare and valued returns means the paths disagree.
                # Fall back to the zero-return rule below only when none
                # carries a value.
                if not all(item.value is None for item in returns):
                    return None
            if not returns or all(item.value is None for item in returns):
                # No return at all, or only bare ones: every path falls off
                # the end, so the body proves `None` (CPython semantics,
                # not a guess). A generator is refused above by the yield
                # scan. The PS1004 fence does not apply: nothing can fall
                # off because nothing expects a value.
                return _type_rt({"kind": "none"})
            inferred = None
            for item in returns:
                assert item.value is not None
                typ = self._infer_type_of(item.value)
                if typ is None or _type_kind(typ) == "none":
                    return None
                if inferred is None:
                    inferred = typ
                elif not types_equal(inferred, typ):
                    return None
        finally:
            self.locals = saved_locals
        assert inferred is not None
        if _has_typevar(inferred):
            return None
        return inferred

    def _infer_yield_type(self, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any] | None:
        """The yielded element type an unannotated generator body proves, or None.

        The yield-side counterpart of `_infer_signature` (issue #500): a
        `def g(...)` carrying `yield` and no return annotation gets the one
        type every yield agrees on, which the coro signature reads as
        `Iterator[elem]` with send and return both None. Only the shape the
        pass lowers without a send or stop type qualifies: no `yield
        from`, no valued `return`, and the yields are collected in the
        function's own scope (a nested def's yields do not vote). Anything
        else returns None and the caller keeps its PS1001 fence:
        inference never guesses.
        """
        if fn.returns is not None or self.function is None or self.inf_peek:
            return None
        if isinstance(fn, ast.AsyncFunctionDef):
            return None
        yields: list[ast.Yield] = []
        stack: list[ast.AST] = list(reversed(fn.body))
        refused = False
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(node, ast.YieldFrom) or (isinstance(node, ast.Return) and node.value is not None):
                refused = True
                break
            if isinstance(node, ast.Yield):
                yields.append(node)
            stack.extend(reversed(list(ast.iter_child_nodes(node))))
        if refused or not yields:
            return None
        saved_locals = self.locals
        saved_fn_body = self.fn_body
        body_locals = dict(_peek_annotated_params(self, fn))
        self.locals = {**saved_locals, **body_locals}
        self.fn_body = list(fn.body)
        try:
            self._yield_bind_locals(fn.body)
            inferred = None
            for node in yields:
                if node.value is None:
                    return None
                typ = self._infer_type_of(node.value)
                if typ is None or _type_kind(typ) == "none":
                    return None
                if inferred is None:
                    inferred = typ
                elif not types_equal(inferred, typ):
                    return None
        finally:
            self.fn_body = saved_fn_body
            self.locals = saved_locals
        if inferred is None or _has_typevar(inferred):
            return None
        return inferred

    def _yield_elem_of_iter(self, iter_node: ast.expr) -> dict[str, Any] | None:
        """The element type a `for` target takes, or None when unproven.

        `range(...)` yields int without a peek; a list, dict, set or str
        iterable yields its element (a dict its keys, bytes its ints), and
        a resumable or iterator class yields the element its class record
        carries. Anything else stays unbound so a yield that reads the
        target refuses the inference.
        """
        if isinstance(iter_node, ast.Call) and isinstance(iter_node.func, ast.Name) and iter_node.func.id == "range":
            return {"kind": "int"}
        typ = self._infer_type_of(iter_node)
        if typ is None:
            return None
        kind = _maybe_type_kind(typ)
        if kind == "list":
            return typ["elem"]
        if kind == "dict":
            return typ["key"]
        if kind == "str":
            return {"kind": "str"}
        if kind == "bytes":
            return {"kind": "int"}
        if kind == "class":
            name = typ.get("name")
            info = self.classes.get(name) if isinstance(name, str) else None
            if info is not None and info.get("iterator") and typ.get("elem") is not None:
                return typ["elem"]
            if info is not None and info.get("coro") and info.get("coro_elem") is not None:
                return info["coro_elem"]
        return None

    def _yield_bind_locals(self, body: list[ast.stmt]) -> None:
        """Bind the body's own local names for the yield-type peek.

        The same source-order walk `_call_site_types` runs for call-site
        pins, trimmed to what a yield value can read: assigns, annotated
        assigns, augmented assigns, `for` targets (range and the iterable
        kinds `_yield_elem_of_iter` proves), and the compound statements
        that nest them. A name bound twice with different types leaves the
        table: the ambiguity refuses the peek rather than guessing.
        """

        def _bind(name: str, typ: dict[str, Any] | None) -> None:
            if typ is None or name in self.bindings or _type_kind(typ) == "none":
                return
            known = self.locals.get(name)
            if known is not None and not types_equal(known, typ):
                self.locals.pop(name, None)
                return
            self.locals[name] = typ

        def _visit(stmts: list[ast.stmt]) -> None:
            for item in stmts:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if isinstance(item, ast.Assign):
                    if len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
                        _bind(item.targets[0].id, self._infer_type_of(item.value))
                    elif (
                        len(item.targets) == 1
                        and isinstance(item.targets[0], ast.Tuple)
                        and all(isinstance(elt, ast.Name) for elt in item.targets[0].elts)
                    ):
                        value_type = self._infer_type_of(item.value)
                        fields = self._tuple_fields_of(value_type)
                        if (
                            fields is None
                            and isinstance(value_type, dict)
                            and _maybe_type_kind(value_type) == "list"
                            and not value_type.get("set")
                        ):
                            fields = [value_type["elem"]] * len(item.targets[0].elts)
                        if fields is not None and len(fields) == len(item.targets[0].elts):
                            for elt, field in zip(item.targets[0].elts, fields, strict=True):
                                _bind(elt.id, field)
                elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    typ = _peek_annotation(item.annotation)
                    if _maybe_type_kind(typ) == "__peek_unresolved__":
                        typ = self._infer_type_of(item.value) if item.value is not None else None
                    _bind(item.target.id, typ)
                elif isinstance(item, ast.AugAssign) and isinstance(item.target, ast.Name):
                    left = self.locals.get(item.target.id)
                    if left is not None:
                        binop = ast.BinOp(
                            left=ast.Name(id=item.target.id, ctx=ast.Load()), op=item.op, right=item.value
                        )
                        ast.copy_location(binop, item)
                        _bind(item.target.id, self._infer_type_of(binop))
                elif isinstance(item, (ast.For, ast.AsyncFor)):
                    if isinstance(item.target, ast.Name):
                        _bind(item.target.id, self._yield_elem_of_iter(item.iter))
                    _visit(item.body)
                    _visit(item.orelse)
                elif isinstance(item, ast.While):
                    _visit(item.body)
                    _visit(item.orelse)
                elif isinstance(item, ast.If):
                    _visit(item.body)
                    _visit(item.orelse)
                elif isinstance(item, ast.With):
                    for entry in item.items:
                        if isinstance(entry.optional_vars, ast.Name):
                            opened = self._infer_type_of(entry.context_expr)
                            if opened is not None and _maybe_type_kind(opened) == "class":
                                _bind(entry.optional_vars.id, opened)
                    _visit(item.body)
                elif isinstance(item, (ast.Try, ast.TryStar)):
                    _visit(item.body)
                    for handler in item.handlers:
                        _visit(handler.body)
                    _visit(item.orelse)
                    _visit(item.finalbody)
                elif isinstance(item, ast.Match):
                    for case in item.cases:
                        _visit(case.body)

        _visit(body)

    def _bridged_signature(self, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any] | None:
        """The return type the checker proved for this def, or None.

        The checker's answer is the fallback for the body's own proof: it
        types the whole body, so it proves a return the subset's peek
        cannot lower (a call the subset refuses, a builtin it does not
        know). Only a type the subset can express is used, and only when
        the checker proved a concrete one (never `Any`).
        """
        if self.bridge is None:
            return None
        proven = self._bridge_return_type(fn)
        if proven is None:
            return None
        facts = self._bridge_def(fn)
        rendered = None
        if facts is not None and facts.return_type is not None:
            rendered = facts.return_type.runtime_repr
        self._record_inference(
            {
                "kind": "return",
                "module": self.module_name,
                "name": fn.name,
                "type": rendered or str(proven),
                "proof": "the checker proved this return type from the function body",
            }
        )
        return proven

    def _bindings_types(self) -> dict[str, dict[str, Any]]:
        """The import-bound modules as peekable types.

        The peek lowers module attribute calls (`os.path.splitext(x)`) and
        needs the module names in `locals` as opaque records the attribute
        path resolves. The binding table already proved each import lowers
        (a bad import fenced in `_live_statements`), so this trusts it.
        """

        out: dict[str, dict[str, Any]] = {}
        for name, binding in self.bindings.items():
            if binding.kind == "module":
                out[name] = _type_rt({"kind": "module", "name": binding.module})
        return out

    def _pin_namespace_locals(
        self, functions: list[ast.FunctionDef | ast.AsyncFunctionDef]
    ) -> list[dict[str, Any]] | None:
        """The Namespace fields the module's `add_argument` calls register.

        The pin pass runs before any body lowers, so a namespace attribute
        argument (`batch_rename(args.work_dir, ...)`) has no field types yet:
        the real `add_argument` lowering registers them later. This reads the
        same closed keyword set from the AST, without lowering anything, so
        the peek can type the attribute. Anything the real handler would
        fence (an unknown keyword, a non-constant name) returns None and the
        pin refuses the parameter, exactly as today.

        The scan covers every function in the module, not one caller: an
        argparse parser is module state, and a script may build it in one
        function (`get_parser`) and read the namespace in another (`main`).
        """

        from pyscriptc.restlower._argparse import peek_namespace_fields

        body: list[ast.stmt] = []
        for fn in functions:
            body.extend(fn.body)
        fields = peek_namespace_fields(self, body)
        if fields is None:
            return None
        info = self.classes.get("Namespace")
        if info is None:
            return None
        # The peek's Namespace layout is the registered one. The real
        # `add_argument` lowering overwrites it with the same records, so
        # the two agree; the peek never runs the real handler.
        info["fields"] = fields
        info["own_fields"] = fields
        return fields

    def _pin_call_site_params(
        self, functions: list[ast.FunctionDef | ast.AsyncFunctionDef], fences: list[Fence]
    ) -> None:
        """Pin unannotated parameters from their call sites (issue #500).

        Runs after the first signature pass, before any body lowers. A
        parameter with no annotation and no default, whose every call site
        passes a value of one proven type, gets that type: the signature
        pass re-runs for the defs that changed, so their return inference
        sees the pinned parameter. Soundness: every call site must agree,
        a parameter any call passes an unprovable value to stays fenced,
        and nothing here reads a def whose signature already exists with
        all parameters annotated. Recursion through the pinned def itself
        is skipped (the call inside its own body is not a pinning site).
        """

        by_name = {fn.name: fn for fn in functions if isinstance(fn, ast.FunctionDef)}
        pending = [
            fn
            for fn in by_name.values()
            if not isinstance(fn, ast.AsyncFunctionDef)
            and any(
                arg.annotation is None and not self._has_default(fn, arg.arg)
                for arg in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
            )
        ]
        if not pending:
            return
        # The first signature pass fenced the unannotated parameters this
        # pass is about to prove. A def the pin rescues has its stale
        # first-pass fence dropped: the rescued signature is the answer and
        # the earlier PS1001 was a state this pass replaces.
        for _round in range(3):
            changed = False
            for fn in pending:
                if fn.name in self.sigs:
                    continue
                unannotated = [
                    arg
                    for arg in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
                    if arg.annotation is None and not self._has_default(fn, arg.arg)
                ]
                if not unannotated:
                    continue
                pins = self._call_site_types(fn, unannotated, by_name)
                if not pins:
                    continue
                for arg, typ in pins.items():
                    arg.annotation = self._pin_annotation(typ)
                saved_prelude = self.prelude
                saved_caught = self.caught
                saved_temp_n = self.temp_n
                saved_function = self.function
                self.prelude = []
                self.caught = []
                self.temp_n = saved_temp_n
                self.function = fn.name
                changed = True
                try:
                    signed = self._signature(fn, None, "method")
                    if signed is None or self.caught:
                        continue
                except Fence:
                    continue
                finally:
                    self.prelude = saved_prelude
                    self.caught = saved_caught
                    self.temp_n = saved_temp_n
                    self.function = saved_function
                self.sigs[fn.name] = signed
                pinned_names = {arg.arg for arg in pins}
                fences[:] = [
                    item
                    for item in fences
                    if not (
                        item.code == "PS1001"
                        and item.function == fn.name
                        and item.message in {f"parameter {name} needs an annotation" for name in pinned_names}
                    )
                ]
            if not changed:
                break

    def _has_default(self, fn: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
        """True when the named positional or keyword-only parameter has a default."""

        plain = fn.args.posonlyargs + fn.args.args
        missing = len(plain) - len(fn.args.defaults)
        for index, arg in enumerate(plain):
            if arg.arg == name:
                return index >= missing
        for arg, default in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=True):
            if arg.arg == name:
                return default is not None
        return False

    def _call_site_types(
        self,
        fn: ast.FunctionDef | ast.AsyncFunctionDef,
        unannotated: list[ast.arg],
        by_name: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    ) -> dict[ast.arg, dict[str, Any]]:
        """The one type every call site proves for each unannotated parameter.

        A call site inside any function body (not the pinned def's own, to
        skip recursion) contributes the argument's peeked type. The first
        disagreement, or a call whose argument does not lower, refuses the
        parameter: no pin.
        """

        wanted = {arg.arg: arg for arg in unannotated}
        pins: dict[str, dict[str, Any]] = {}

        def _call_site_locals(caller: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            """Bind the caller body's own local names for the argument peeks.

            `get_category(ext)` passes a local the caller bound earlier; the
            pin pass runs before any body lowers, so the peek needs those
            names. The walk is recursive in source order (`if`/`for`/`while`
            bodies hold the binding the call site reads, like
            `file_names = sys.argv[1:]` under an `if len(sys.argv)`), and a
            name bound twice with different types leaves the table: the
            ambiguity refuses the pin rather than guessing. Loop targets
            resolve from their iterable when its type is known.
            """

            self.locals = self._bindings_types()

            def _bind(name: str, typ: dict[str, Any] | None) -> None:
                if typ is None or _type_kind(typ) == "none":
                    return
                known = self.locals.get(name)
                if known is not None and not types_equal(known, typ):
                    self.locals.pop(name, None)
                    return
                self.locals[name] = typ

            def _ns_type() -> dict[str, Any] | None:
                """The Namespace type, when the caller registered arguments.

                `args = parser.parse_args()` lowers late; the pin walk binds
                the local so an attribute argument (`args.work_dir`) peeks
                through the field table the add_argument scan built.
                """

                if self.classes.get("Namespace", {}).get("fields"):
                    return _type_rt({"kind": "class", "name": "Namespace"})
                return None

            def _assign_type(value: ast.expr) -> dict[str, Any] | None:
                if (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Attribute)
                    and value.func.attr == "parse_args"
                    and (typ := _ns_type()) is not None
                ):
                    return typ
                return self._infer_type_of(value)

            def _visit(body: list[ast.stmt]) -> None:
                for item in body:
                    if isinstance(item, ast.Assign) and isinstance(item.targets[0], ast.Name):
                        if item.targets[0].id in self.bindings:
                            continue
                        _bind(item.targets[0].id, _assign_type(item.value))
                    elif (
                        isinstance(item, ast.Assign)
                        and len(item.targets) == 1
                        and isinstance(item.targets[0], ast.Tuple)
                        and all(isinstance(elt, ast.Name) for elt in item.targets[0].elts)
                    ):
                        # `_, ext = os.path.splitext(name)`: a fixed-arity
                        # tuple decodes like the return peek's unpack, each
                        # name taking its field's type. A list RHS decodes
                        # as its element type for every name: the real
                        # unpack length-checks at run time and a length
                        # mismatch is CPython's ValueError, so the binding
                        # is exactly as proven as the unpack itself.
                        value_type = self._infer_type_of(item.value)
                        fields = self._tuple_fields_of(value_type)
                        if (
                            fields is None
                            and isinstance(value_type, dict)
                            and _maybe_type_kind(value_type) == "list"
                            and not value_type.get("set")
                        ):
                            fields = [value_type["elem"]] * len(item.targets[0].elts)
                        if fields is not None and len(fields) == len(item.targets[0].elts):
                            for elt, field in zip(item.targets[0].elts, fields, strict=True):
                                _bind(elt.id, field)
                    if isinstance(item, ast.AugAssign) and isinstance(item.target, ast.Name):
                        left = self.locals.get(item.target.id)
                        if left is not None:
                            binop = ast.BinOp(
                                left=ast.Name(id=item.target.id, ctx=ast.Load()), op=item.op, right=item.value
                            )
                            ast.copy_location(binop, item)
                            _bind(item.target.id, self._infer_type_of(binop))
                    if isinstance(item, ast.For) and isinstance(item.target, ast.Name):
                        iter_typ = self._infer_type_of(item.iter)
                        if iter_typ is not None and _maybe_type_kind(iter_typ) in {"list", "dict"}:
                            elem = iter_typ["elem"] if _type_kind(iter_typ) == "list" else iter_typ["key"]
                            _bind(item.target.id, elem)
                    if isinstance(item, ast.If):
                        _visit(item.body)
                        _visit(item.orelse)
                    if isinstance(item, (ast.For, ast.While)):
                        if isinstance(item, ast.For) and isinstance(item.target, ast.Name):
                            iter_typ = self._infer_type_of(item.iter)
                            if iter_typ is not None and _maybe_type_kind(iter_typ) in {"list", "dict"}:
                                elem = iter_typ["elem"] if _type_kind(iter_typ) == "list" else iter_typ["key"]
                                _bind(item.target.id, elem)
                        _visit(item.body)
                        _visit(item.orelse)
                    if isinstance(item, ast.With):
                        # `with open(p) as f` binds the handle local like the
                        # real lowering does; a general context manager is
                        # refused by its own expression peek.
                        named = [
                            it.optional_vars
                            for it in item.items
                            if it.optional_vars is not None and isinstance(it.optional_vars, ast.Name)
                        ]
                        for it in item.items:
                            if isinstance(it.optional_vars, ast.Name):
                                opened = self._infer_type_of(it.context_expr)
                                if opened is not None and _maybe_type_kind(opened) == "class":
                                    _bind(it.optional_vars.id, opened)
                        if len(item.items) == 1 or named:
                            _visit(item.body)
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        continue

            # Annotated parameters of the caller are facts the def states,
            # not guesses: an argument the call site passes as a bare
            # parameter (`format_array("t", [n])`) peeks through them. The
            # pins from the caller's own rescue ride along the same way.
            # They bind before the walk: a local's own value may read a
            # parameter (`f = open(fname)`), and the peek needs it then.
            for arg in caller.args.posonlyargs + caller.args.args + caller.args.kwonlyargs:
                if arg.annotation is None:
                    continue
                typ = _peek_annotation(arg.annotation)
                if _maybe_type_kind(typ) not in {None, "__peek_unresolved__"} and arg.arg not in self.bindings:
                    self.locals[arg.arg] = typ
            _visit(caller.body)

        saved_pin_locals = self.locals
        self._pin_namespace_locals(list(by_name.values()))
        for caller in by_name.values():
            _call_site_locals(caller)
            for node in ast.walk(caller):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                if node.func.id != fn.name:
                    continue
                if caller is fn:
                    continue
                for index, arg_node in enumerate(node.args):
                    plain = fn.args.posonlyargs + fn.args.args
                    if index >= len(plain) or plain[index].arg not in wanted:
                        continue
                    name = plain[index].arg
                    typ = self._infer_type_of(arg_node)
                    if typ is None or _type_kind(typ) == "none":
                        pins.pop(name, None)
                        wanted.pop(name, None)
                        continue
                    if name in pins and not types_equal(pins[name], typ):
                        pins.pop(name, None)
                        wanted.pop(name, None)
                        continue
                    if name in wanted:
                        pins[name] = typ
                for kw in node.keywords:
                    if kw.arg is None or kw.arg not in wanted:
                        continue
                    typ = self._infer_type_of(kw.value)
                    if typ is None or _type_kind(typ) == "none":
                        pins.pop(kw.arg, None)
                        wanted.pop(kw.arg, None)
                        continue
                    if kw.arg in pins and not types_equal(pins[kw.arg], typ):
                        pins.pop(kw.arg, None)
                        wanted.pop(kw.arg, None)
                        continue
                    if kw.arg in wanted:
                        pins[kw.arg] = typ
        self.locals = saved_pin_locals
        out: dict[ast.arg, dict[str, Any]] = {}
        for name, typ in pins.items():
            if name in wanted:
                out[wanted[name]] = typ
        return out

    def _pin_annotation(self, typ: dict[str, Any]) -> ast.AST:
        """The annotation spelling for a pinned parameter type, or a poison one.

        `_annotation` re-reads the annotation when the signature pass
        re-runs, so the spelling must round-trip: scalars spell as
        themselves, lists and dicts as generics, a class as its name. A
        type with no spelling here means the pin is refused (the caller
        never sees it: `_call_site_types` only produces the subset this
        helper reads).
        """

        kind = _maybe_type_kind(typ)
        if kind in {"int", "float", "bool", "str", "bytes"}:
            return ast.Name(id=kind, ctx=ast.Load())
        if kind == "list" and typ.get("set"):
            return ast.Subscript(
                value=ast.Name(id="set", ctx=ast.Load()), slice=ast.Name(id="int", ctx=ast.Load()), ctx=ast.Load()
            )
        if kind == "list":
            elem = self._pin_annotation(typ["elem"])
            return ast.Subscript(value=ast.Name(id="list", ctx=ast.Load()), slice=elem, ctx=ast.Load())
        if kind == "dict":
            key = self._pin_annotation(typ["key"])
            value = self._pin_annotation(typ["value"])
            return ast.Subscript(
                value=ast.Name(id="dict", ctx=ast.Load()),
                slice=ast.Tuple(elts=[key, value], ctx=ast.Load()),
                ctx=ast.Load(),
            )
        if kind == "class":
            return ast.Name(id=typ["name"], ctx=ast.Load())
        return ast.Name(id="__pin_unsupported__", ctx=ast.Load())

    def _infer_fence(self, node: ast.AST, target: str) -> NoReturn:
        """The refusal fence, byte-identical to the pre-inference message.

        The rule and the hint stay where the reader looks them up: the
        `param-inference` / `local-inference` / `return-inference` families
        carry the refactor hint, and `pyscriptc explain PS1001` the rule.
        Ambiguity (a bare `None`, a lambda, an empty display, a call that
        does not lower) is a fence, never a guess.
        """
        self.fail(node, "PS1001", f"{target} needs an annotation before use")
