"""Resumable functions: generators and `async def` coroutines.

Design: `.agents/plans/2026-10-21-async-threads-design.md` (stackless state
machines in existing IR, no new IR kinds). Two halves live here:

- `CoroMixin`: lower-time hooks mixed into `Lowerer`. A module-level
  generator gets an outward signature that returns its frame class
  (`gen.<name>`, with `__iter__`/`__next__`), so `for x in g()` lowers through
  the existing `_for_dunder` path. Its body lowers as a `None` function in
  which each `yield v` statement becomes a suspend marker: an `expr` statement
  calling the builtin `__ps_yield__`.
- `lower_resumables`: the IR-to-IR pass. It turns each marked function into a
  frame class plus a wrapper that allocates the frame. The resume method
  copies frame fields into locals, re-enters the structured body through
  guards (`if`/`while` re-entry without re-evaluating their conditions), and
  copies locals back to the frame at each suspension. The output uses only
  existing IR kinds, so `llvm.py`, `canonical.py` and every IR hash of a
  program without generators are unchanged.

A coroutine is the second `_Kind`: the marker is `__ps_await__`, the resume
method is `resume(self) -> int` (0 pending, 1 done) and the frame carries a
`result` field; the guard transform is shared.

Await protocol. `await g()` lowers to a marker call `__ps_await__(child)`
whose argument is the awaited frame. The IR pass rewrites the marker into
the poll loop: the parent stores the child frame in a local, calls
`child.resume()`, and suspends with status 0 while the child is pending. On
resume the parent re-polls the same child and, once it reports done, reads
the child's `result` field into the awaited value. A coroutine body ends with
`return v`, which the pass turns into `self.result = v; return 1`.

The event loop lives in the C runtime (`runtime/ps_loop.c`): a task list, a
run-to-completion driver and a monotonic clock. `asyncio.run(coro)` wraps the
root frame in a task and drives it; `asyncio.gather` wraps several frames in
tasks and drives them together, so the children interleave exactly like
CPython's.

Scope today: module-level generators annotated `Iterator[T]`, `Iterable[T]` or
`Generator[T, None, None]`, module-level `async def` coroutines with a return
annotation, `yield v` and `yield from <iterable>` as statements, bare
`return`, yields under `if`/`while`/`for`, `await <coroutine call>` as a
statement or the whole value of an assignment, `asyncio.run`,
`asyncio.sleep` and `asyncio.gather`. Not yet: yield inside try/with, awaits
inside try/with, `send`/`throw`/`close`, nested or generic or decorated
resumables, methods, package units, `create_task` with independent lifetimes.
`PYSC_CORO=0` turns the lowering off (generators and coroutines fence as
before).
"""

from __future__ import annotations

import ast
import copy
import os
from dataclasses import dataclass, field
from typing import Any

from pyscriptc.errors import span_of

YIELD_MARKER = "__ps_yield__"
AWAIT_MARKER = "__ps_await__"
SLEEP_MARKER = "__ps_sleep__"
RETURN_MARKER = "__ps_return__"
STATE = ".ps_state"
RESULT = ".ps_result"
TASK = ".ps_task"
SELF = ".ps_self"
RESUME = ".ps_rs"
SEND = ".ps_send"
AWAIT_RESULT_PREFIX = ".ps_awv_"
DONE = -1
PENDING = 0
COMPLETE = 1

_INT = {"kind": "int"}
_BOOL = {"kind": "bool"}
_NONE = {"kind": "none"}
_FLOAT = {"kind": "float"}
# The resume shim's outward type: every coroutine's callable cell carries it.
_RESUME_FUNC = {"kind": "func", "params": [{"kind": "class", "name": "@frame"}], "return": {"kind": "int"}}
_GENERATOR_FORMS = {"Iterator", "Iterable", "Generator"}


class CoroError(Exception):
    """A resumable body shape the pass does not lower yet (becomes a PS2002 fence)."""

    def __init__(self, message: str, span: dict[str, Any]) -> None:
        super().__init__(message)
        self.message = message
        self.span = span


@dataclass
class FramePlan:
    """What the lowerer decided for one resumable function."""

    kind: str  # "generator" or "coroutine"
    function: str
    frame: str
    elem: dict[str, Any]  # yield element (generator) or return value (coroutine)
    params: list[dict[str, Any]] = field(default_factory=list)
    ir_name: str = ""  # the wrapper's IR name (`pkg.mod.g` in a package unit)
    send: dict[str, Any] = field(default_factory=lambda: dict(_NONE))  # the Generator[T, S, R] send type


def enabled() -> bool:
    return os.environ.get("PYSC_CORO", "1") != "0"


def is_generator(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(isinstance(node, (ast.Yield, ast.YieldFrom)) for node in _own_nodes(fn.body))


def _own_nodes(body: list[ast.stmt]):
    """Every node of a function body, not descending into nested scopes."""
    stack: list[ast.AST] = list(reversed(body))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


def _shape_error(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[ast.AST, str] | None:
    """The first body construct outside today's resumable scope, or None."""

    coroutine = isinstance(fn, ast.AsyncFunctionDef)

    def visit(stmts: list[ast.stmt], in_try: bool) -> tuple[ast.AST, str] | None:
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return stmt, "a nested def or class in a resumable is not lowered yet"
            if isinstance(stmt, ast.Return):
                if stmt.value is not None and not (isinstance(stmt.value, ast.Constant) and stmt.value.value is None):
                    if not coroutine:
                        return stmt, "return with a value in a generator is not lowered yet"
                if in_try:
                    return stmt, "return inside try or with in a resumable is not lowered yet"
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, (ast.Yield, ast.YieldFrom)):
                if in_try:
                    return stmt, "yield inside try or with is not lowered yet"
                inner = [stmt.value.value] if stmt.value.value is not None else []
            elif isinstance(stmt, (ast.Assign, ast.AnnAssign)) and isinstance(stmt.value, ast.Yield) and not coroutine:
                # `x = yield v` / `x: T = yield v`: send support. Only the
                # plain single-name form; anything else stays fenced below.
                if in_try:
                    return stmt, "yield inside try or with is not lowered yet"
                target = stmt.targets[0] if isinstance(stmt, ast.Assign) else stmt.target
                if not isinstance(target, ast.Name):
                    return stmt, "yield as an expression (send) is not lowered yet"
                inner = [stmt.value.value] if stmt.value.value is not None else []
            else:
                inner = [child for child in ast.iter_child_nodes(stmt) if isinstance(child, ast.expr)]
            for expr in inner:
                for node in ast.walk(expr):
                    if isinstance(node, (ast.Yield, ast.YieldFrom)):
                        return node, "yield as an expression (send) is not lowered yet"
                    if isinstance(node, ast.Await):
                        if not coroutine:
                            return node, "await outside an async function is not lowered"
                        if in_try:
                            return node, "await inside try or with is not lowered yet"
                    if isinstance(node, ast.Lambda):
                        return node, "a lambda in a resumable is not lowered yet"
            nested_try = in_try or isinstance(stmt, (ast.Try, ast.TryStar, ast.With, ast.AsyncWith))
            for name in ("body", "orelse", "finalbody"):
                block = getattr(stmt, name, None)
                if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                    found = visit(block, nested_try)
                    if found:
                        return found
            for handler in getattr(stmt, "handlers", []) or []:
                found = visit(handler.body, True)
                if found:
                    return found
            for case in getattr(stmt, "cases", []) or []:
                found = visit(case.body, in_try)
                if found:
                    return found
        return None

    return visit(fn.body, False)


class CoroMixin:
    """Lower-time hooks. `Lowerer.module` calls `_coro_signature`,
    `_coro_body_sig` and `_coro_finish`; `_stmt_core` is extended here."""

    def _coro_plans(self) -> dict[str, FramePlan]:
        return self.__dict__.setdefault("_coro_plan_table", {})

    def _coro_signature(
        self, fn: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
        """The outward signature of a module-level resumable, or None for any
        other function (the caller then uses `_signature` as before)."""
        if not enabled() or fn.decorator_list:
            return None
        if self.cataloguing and not (is_generator(fn) or isinstance(fn, ast.AsyncFunctionDef)):
            return None
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or not (
            is_generator(fn) or isinstance(fn, ast.AsyncFunctionDef)
        ):
            return None
        coroutine = isinstance(fn, ast.AsyncFunctionDef)
        elem = self._coro_types(fn)[0] if not coroutine else self._coro_result(fn)
        send = dict(_NONE) if coroutine else self._coro_types(fn)[1]
        shape = _shape_error(fn)
        if shape is not None:
            self.fail(shape[0], "PS2002", shape[1])
        plain = copy.copy(fn)
        plain.returns = ast.Constant(value=None)
        params, _ = self._signature(plain, None, "method")
        from pyscriptc.lower._core import _has_typevar

        if _has_typevar(elem) or any(_has_typevar(param["type"]) for param in params):
            self.fail(fn, "PS2002", "a generic resumable is not lowered yet")
        frame = f"{'coro' if coroutine else 'gen'}.{fn.name}"
        frame_t = {"kind": "class", "name": frame}
        self_t = [{"name": "self", "type": frame_t}]
        if coroutine:
            methods: dict[str, Any] = {"resume": (self_t, _INT)}
        else:
            methods = {
                "__iter__": (self_t, frame_t),
                "__next__": (self_t, elem),
                "close": (self_t, _NONE),
            }
            if send.get("kind") != "none":
                methods["send"] = (self_t + [{"name": ".ps_value", "type": send}], elem)
        self.classes[frame] = {
            "fields": [],
            "own_fields": [],
            "methods": methods,
            "method_kind": {},
            "method_owner": {},
            "base": None,
            "coro": "coroutine" if coroutine else "generator",
            "coro_elem": copy.deepcopy(elem),
        }
        self.known_classes.add(frame)
        self._coro_plans()[fn.name] = FramePlan(
            "coroutine" if coroutine else "generator",
            fn.name,
            frame,
            elem,
            params,
            self._ir_name(fn.name),
            send,
        )
        return params, frame_t

    def _coro_result(self, fn: ast.AsyncFunctionDef) -> dict[str, Any]:
        """A coroutine's result type is its return annotation."""
        if fn.returns is None:
            self.fail(fn, "PS1001", f"{fn.name} needs a return annotation")
        return self._annotation(fn.returns)

    def _coro_elem(self, fn: ast.FunctionDef) -> dict[str, Any]:
        return self._coro_types(fn)[0]

    def _coro_types(self, fn: ast.FunctionDef) -> tuple[dict[str, Any], dict[str, Any]]:
        """The yield element type and the send type of a generator.

        `Iterator[T]` and `Generator[T, None, None]` send None; a
        `Generator[T, S, R]` sends S. The return type R is not lowered yet.
        """
        ann = fn.returns
        if ann is None:
            self.fail(fn, "PS1001", f"{fn.name} needs a return annotation")
        form = self._typing_alias(ann.value) if isinstance(ann, ast.Subscript) else None
        if form not in _GENERATOR_FORMS:
            self.fail(ann, "PS1001", "a generator is annotated Iterator[T], Iterable[T] or Generator[T, None, None]")
        args = list(ann.slice.elts) if isinstance(ann.slice, ast.Tuple) else [ann.slice]
        send: dict[str, Any] = dict(_NONE)
        if form == "Generator":
            if len(args) not in {1, 3}:
                self.fail(ann, "PS1001", "Generator is Generator[T] or Generator[T, S, R]")
            if len(args) == 3:
                if not (isinstance(args[2], ast.Constant) and args[2].value is None):
                    self.fail(ann, "PS2002", "Generator[T, S, R] with a return type is not lowered yet")
                if not (isinstance(args[1], ast.Constant) and args[1].value is None):
                    send = self._annotation(args[1])
        elif len(args) != 1:
            self.fail(ann, "PS1001", f"{form}[T] takes one type")
        return self._annotation(args[0]), send

    def _coro_body_sig(self, name: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Signature the body lowers under: a resumable body returns None."""
        plan = self._coro_plans().get(name)
        self.__dict__["_coro_active"] = plan
        params, ret = self.sigs[name]
        return (params, dict(_NONE)) if plan is not None else (params, ret)

    def _coro_method_signature(
        self, fn: ast.FunctionDef | ast.AsyncFunctionDef, class_name: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
        """The outward signature of a resumable method, or None for a plain method.

        A generator method returns its frame class: `C.items()` yields a
        `gen.C.items` frame, and the caller consumes it through the same
        `__iter__`/`__next__` path a module-level generator uses. A coroutine
        method (`async def`) returns its `coro.C.<name>` frame the same way;
        the caller awaits it (issue #178, stage A).
        """
        coroutine = isinstance(fn, ast.AsyncFunctionDef)
        if not enabled() or fn.decorator_list or not (is_generator(fn) or coroutine):
            return None
        if coroutine:
            elem = self._coro_result(fn)
            send = dict(_NONE)
        else:
            elem, send = self._coro_types(fn)
        shape = _shape_error(fn)
        if shape is not None:
            self.fail(shape[0], "PS2002", shape[1])
        plain = copy.copy(fn)
        plain.returns = ast.Constant(value=None)
        params, _ = self._signature(plain, class_name, "method")
        from pyscriptc.lower._core import _has_typevar

        if _has_typevar(elem) or any(_has_typevar(param["type"]) for param in params):
            self.fail(fn, "PS2002", "a generic resumable is not lowered yet")
        prefix = "coro" if coroutine else "gen"
        frame = f"{prefix}.{class_name}.{fn.name}"
        frame_t = {"kind": "class", "name": frame}
        self_t = [{"name": "self", "type": frame_t}]
        if coroutine:
            method_sigs: dict[str, Any] = {"resume": (self_t, _INT)}
        else:
            method_sigs = {
                "__iter__": (self_t, frame_t),
                "__next__": (self_t, elem),
                "close": (self_t, _NONE),
                **(
                    {"send": (self_t + [{"name": ".ps_value", "type": send}], elem)}
                    if send.get("kind") != "none"
                    else {}
                ),
            }
        self.classes[frame] = {
            "fields": [],
            "own_fields": [],
            "methods": method_sigs,
            "method_kind": {},
            "method_owner": {},
            "base": None,
            "coro": "coroutine" if coroutine else "generator",
            "coro_elem": copy.deepcopy(elem),
        }
        self.known_classes.add(frame)
        key = f"{class_name}.{fn.name}"
        self._coro_plans()[key] = FramePlan(
            "coroutine" if coroutine else "generator",
            key,
            frame,
            elem,
            params,
            "",
            send,
        )
        return params, frame_t

    def _coro_method_body_sig(
        self, class_name: str, method: str, params: list[dict[str, Any]], ret: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """The signature a generator method's body lowers under.

        The body returns None (the frame carries the values), so the method
        signature's frame type is replaced by None for the body.
        """
        plan = self._coro_plans().get(f"{class_name}.{method}")
        self.__dict__["_coro_active"] = plan
        return (params, dict(_NONE)) if plan is not None else (params, ret)

    def _coro_current(self) -> FramePlan | None:
        plan = self.__dict__.get("_coro_active")
        if plan is None or self.function != plan.function:
            return None
        return plan

    def _stmt_core(self, stmt: ast.stmt) -> list[dict[str, Any]]:
        plan = self._coro_current()
        if plan is not None:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Yield):
                return self._coro_yield(stmt, stmt.value, plan)
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.YieldFrom):
                return self._coro_yield_from(stmt, stmt.value)
            if (
                isinstance(stmt, (ast.Assign, ast.AnnAssign))
                and isinstance(stmt.value, ast.Yield)
                and plan.kind == "generator"
            ):
                # `x = yield v` / `x: T = yield v`: the sent value arrives in
                # a frame local the marker names; the assignment reads it
                # after the marker.
                target = stmt.targets[0] if isinstance(stmt, ast.Assign) else stmt.target
                assert isinstance(target, ast.Name)
                recv = self._coro_recv_temp(plan.send)
                marker = ast.Expr(value=stmt.value)
                ast.copy_location(marker, stmt)
                marker.end_lineno, marker.end_col_offset = stmt.end_lineno, stmt.end_col_offset
                stmts = self._coro_yield(marker, stmt.value, plan, recv)
                span = span_of(stmt, self.filename)
                typ = self.locals.get(target.id) or plan.send
                if isinstance(stmt, ast.AnnAssign):
                    typ = self._annotation(stmt.annotation)
                    self.locals[target.id] = typ
                stmts.append(_assign(target.id, _name(recv, typ, span), span))
                return stmts
            if isinstance(stmt, ast.Return):
                span = span_of(stmt, self.filename)
                if plan.kind == "coroutine" and stmt.value is not None:
                    # The result travels in the frame's `result` field; the
                    # body itself returns None.
                    value = self._expr(stmt.value, plan.elem)
                    self._expect(stmt.value, value["type"], plan.elem, "return")
                    call = {
                        "kind": "call",
                        "callee": {"kind": "builtin", "name": RETURN_MARKER},
                        "args": [value],
                        "type": dict(_NONE),
                        "span": span,
                    }
                    return [{"kind": "return", "value": call, "span": span}]
                return [{"kind": "return", "value": _const(None, _NONE, span), "span": span}]
        return super()._stmt_core(stmt)

    def _coro_recv_temp(self, send_t: dict[str, Any]) -> str:
        """A frame local holding the value a `send()` delivers at one yield."""
        recvs: list[str] = self.__dict__.setdefault("_coro_recvs", [])
        name = f".ps_recv_{len(recvs) + 1}"
        recvs.append(name)
        if send_t.get("kind") != "none":
            self.locals[name] = send_t
        return name

    def _coro_yield(
        self, stmt: ast.Expr, node: ast.Yield, plan: FramePlan, recv: str | None = None
    ) -> list[dict[str, Any]]:
        span = span_of(stmt, self.filename)
        if node.value is None:
            value = _const(None, dict(_NONE), span)
        else:
            value = self._expr(node.value, plan.elem)
            self._expect(node, value["type"], plan.elem, "yield")
        call = {
            "kind": "call",
            "callee": {"kind": "builtin", "name": YIELD_MARKER},
            "args": [value],
            "type": dict(_NONE),
            "span": span,
        }
        if recv is not None:
            call["recv"] = recv
            call["recvType"] = dict(plan.send)
        return [{"kind": "expr", "expr": call, "span": span}]

    def _coro_yield_from(self, stmt: ast.Expr, node: ast.YieldFrom) -> list[dict[str, Any]]:
        """`yield from it` (no send delegation) is `for t in it: yield t`."""
        temp = self._temp("yf")
        target = ast.Name(id=temp, ctx=ast.Store())
        inner = ast.Expr(value=ast.Yield(value=ast.Name(id=temp, ctx=ast.Load())))
        loop = ast.For(target=target, iter=node.value, body=[inner], orelse=[], type_comment=None)
        for item in (target, inner, inner.value, inner.value.value, loop):
            ast.copy_location(item, stmt)
            item.end_lineno, item.end_col_offset = stmt.end_lineno, stmt.end_col_offset
        return self._stmt(loop)

    # --- await ------------------------------------------------------------

    def _coro_await_type(self, node: ast.Await) -> dict[str, Any]:
        """The result type of `await <expr>`: the awaited coroutine's result."""
        value = self._expr(node.value)
        kind = value["type"].get("kind")
        if kind != "class" or not str(value["type"].get("name", "")).startswith("coro."):
            self.fail(node, "PS2002", "await takes a coroutine call, not another value")
        plan = self._coro_plan_for_frame(value["type"]["name"])
        if plan is None:
            self.fail(node, "PS2002", "await takes a coroutine call, not another value")
        return plan.elem

    def _coro_plan_for_frame(self, frame: str) -> FramePlan | None:
        """The plan for a frame class: the local plan table first, then the
        export table (a frame another package unit defined, stage B)."""
        for plan in self._coro_plans().values():
            if plan.frame == frame:
                return plan
        return self._coro_plan_from_exports(frame)

    def _coro_plan_from_exports(self, frame: str) -> FramePlan | None:
        """Build a plan for a foreign frame from the export table.

        The export table carries what the await needs: the frame class shape
        (the imported class info names `resume` as its only method and `coro`
        as its kind) and `coro_elem`, the coroutine's result type. The
        consumer reads the same shape the defining module emitted; only the
        poll side runs here, so `params` and `ir_name` stay empty.
        """
        for exports in (self.package or {}).values():
            item = exports.get(frame)
            if item is None or item.get("kind") != "class":
                continue
            info = item.get("info") or {}
            if info.get("coro") != "coroutine" or info.get("coro_elem") is None:
                continue
            plan = FramePlan("coroutine", "", frame, info["coro_elem"])
            self._coro_plans()[f"@foreign.{frame}"] = plan
            return plan
        return None

    def _expr_any(self, node: ast.expr, expect: dict[str, Any] | None = None) -> dict[str, Any]:
        if isinstance(node, ast.Await) and self._coro_current() is not None:
            return self._coro_await(node)
        return super()._expr_any(node, expect)

    def _coro_await(self, node: ast.Await) -> dict[str, Any]:
        """`await child()` lowers to a poll loop over the child's frame.

        The awaited value is a coroutine frame (`coro.<name>`), so the await
        keeps that frame in a local and polls it with `resume()`. The poll is
        the parent's suspension point: while the child is pending the parent
        suspends with status 0 and re-polls the same child on resume.

        `await` of anything else (today `asyncio.gather`, whose prelude
        already drove its tasks on the run loop) is a plain value: the pass
        keeps the poll machinery out and the await reads straight through.
        """
        plan = self._coro_current()
        assert plan is not None
        span = span_of(node, self.filename)
        child = self._expr(node.value)
        child_t = child["type"]
        if child_t.get("kind") == "class" and str(child_t.get("name", "")).startswith("coro."):
            child_plan = self._coro_plan_for_frame(child_t["name"])
            if child_plan is None:
                self.fail(node, "PS2002", "await takes a compiled coroutine call")
            # The child frame lives in a local: the parent's copy-out at the
            # suspension keeps it alive across the resume.
            slot = self._temp("aw")
            self.locals[slot] = child_t
            result = _await_result_name(slot)
            self.locals[result] = child_plan.elem
            self.prelude.append(_assign(slot, child, span))
            marker = {
                "kind": "call",
                "callee": {"kind": "builtin", "name": AWAIT_MARKER},
                "args": [_name(slot, child_t, span)],
                "type": dict(_NONE),
                "span": span,
            }
            self.prelude.append({"kind": "expr", "expr": marker, "span": span})
            # The awaited value: the child's result field once it completes.
            return _name(result, child_plan.elem, span)
        return child

    def _coro_finish(self, module: dict[str, Any]) -> dict[str, Any]:
        """Run the IR pass; a module without resumables is returned as is."""
        plans = self._coro_plans()
        self.__dict__["_coro_active"] = None
        if not plans:
            return module
        return lower_resumables(module, plans)


# --- IR pass ---------------------------------------------------------------


def lower_resumables(module: dict[str, Any], plans: dict[str, FramePlan]) -> dict[str, Any]:
    """Rewrite every planned function into a frame class and a wrapper."""
    functions: list[dict[str, Any]] = []
    classes = list(module["classes"])
    fences = list(module["fences"])
    for fn in module["functions"]:
        plan = plans.get(fn["name"]) or _plan_by_ir_name(plans, fn["name"])
        if plan is None:
            functions.append(fn)
            continue
        try:
            wrapper, frame, forwarder = _Frame(fn, plan, _KINDS[plan.kind], plans).build()
        except CoroError as exc:
            fences.append({"code": "PS2002", "message": exc.message, "function": fn["name"], "span": exc.span})
            continue
        functions.append(wrapper)
        if forwarder is not None:
            functions.append(forwarder)
        classes.append(frame)
    classes, method_functions = _lower_method_resumables(classes, plans, fences)
    functions.extend(method_functions)
    return {**module, "functions": functions, "classes": classes, "fences": fences}


def _lower_method_resumables(
    classes: list[dict[str, Any]], plans: dict[str, FramePlan], fences: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rewrite resumable methods the same way module functions rewrite.

    A planned method's body record is the resume body: the pass swaps in the
    frame-allocating wrapper as the method and appends the frame class. The
    plan is keyed `Class.method`, the same qualified name the body lowered
    under. Both kinds rewrite here; a coroutine method's forwarder comes
    back so the run loop can drive it like a function frame.
    """
    out: list[dict[str, Any]] = []
    method_functions: list[dict[str, Any]] = []
    for cls in classes:
        methods: list[dict[str, Any]] = []
        for method in cls["methods"]:
            plan = plans.get(f"{cls['name']}.{method['name']}")
            if (
                plan is None
                or plan.frame != f"{'coro' if plan.kind == 'coroutine' else 'gen'}.{cls['name']}.{method['name']}"
            ):
                methods.append(method)
                continue
            try:
                wrapper, frame, forwarder = _Frame(method, plan, _KINDS[plan.kind], plans).build()
            except CoroError as exc:
                fences.append({"code": "PS2002", "message": exc.message, "function": method["name"], "span": exc.span})
                methods.append(method)
                continue
            methods.append(wrapper)
            out.append(frame)
            if forwarder is not None:
                # A coroutine method's resume cell names this module-level
                # forwarder, the same symbol shape a module-level coroutine
                # uses (`coro.<name>.resume`), so ps_loop.c stays uniform.
                method_functions.append(forwarder)
        out.append({**cls, "methods": methods})
    return out, method_functions


def _plan_by_ir_name(plans: dict[str, FramePlan], ir_name: str) -> FramePlan | None:
    """The plan whose wrapper carries this IR name.

    A package unit's functions are named `pkg.mod.g` in the IR, while the
    plan is keyed by the source name `g`.
    """
    for plan in plans.values():
        if plan.ir_name and plan.ir_name == ir_name:
            return plan
    return None


class _Kind:
    """What a suspension and a completion mean for one resumable flavour."""

    marker: str

    def methods(self, frame: _Frame, resume: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError

    def suspend(self, frame: _Frame, value: dict[str, Any], span: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError

    def finish(self, frame: _Frame, span: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError


class _GeneratorKind(_Kind):
    """`__next__` returns the yielded value; completion raises StopIteration
    (code 8), which `_for_dunder` turns into the loop exit.

    `close` marks the frame done (the next `__next__` raises StopIteration,
    as CPython does after a close), and `throw` raises the given exception
    out of the generator. Neither can be caught inside the generator: a
    `yield` inside `try` is fenced, so no handler can surround a live
    suspension point and CPython's "the generator catches it" outcome is
    unreachable. Propagating out is therefore the exact semantics for
    every program that lowers.
    """

    marker = YIELD_MARKER

    def methods(self, frame: _Frame, resume: dict[str, Any]) -> list[dict[str, Any]]:
        span = frame.span
        this = frame.self_name(span)
        iter_m = {
            "name": "__iter__",
            "params": [{"name": SELF, "type": frame.frame_t}],
            "returnType": frame.frame_t,
            "locals": [{"name": SELF, "type": frame.frame_t}],
            "body": [{"kind": "return", "value": this, "span": span}],
            "span": span,
        }
        next_m = {**resume, "name": "__next__", "returnType": frame.plan.elem}
        close_m = {
            "name": "close",
            "params": [{"name": SELF, "type": frame.frame_t}],
            "returnType": dict(_NONE),
            "locals": [{"name": SELF, "type": frame.frame_t}],
            "body": [frame.field_set(STATE, _const(DONE, _INT, span), span)],
            "span": span,
        }
        out = [iter_m, next_m, close_m]
        if frame.plan.send.get("kind") != "none":
            # `send(v)`: store v into the frame, then resume. A value sent to
            # a generator that never ran is ignored (a documented divergence:
            # CPython raises TypeError, which this subset cannot express).
            send_t = frame.plan.send
            step = {
                "kind": "call",
                "callee": {"kind": "function", "name": f"cls.{frame.plan.frame}.__next__"},
                "args": [this],
                "type": frame.plan.elem,
                "span": span,
            }
            send_m = {
                "name": "send",
                "params": [{"name": SELF, "type": frame.frame_t}, {"name": ".ps_value", "type": send_t}],
                "returnType": frame.plan.elem,
                "locals": [
                    {"name": SELF, "type": frame.frame_t},
                    {"name": ".ps_value", "type": send_t},
                ],
                "body": [
                    frame.field_set(SEND, _name(".ps_value", send_t, span), span),
                    {"kind": "return", "value": step, "span": span},
                ],
                "span": span,
            }
            out.append(send_m)
        return out

    def suspend(self, frame: _Frame, value: dict[str, Any], span: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"kind": "return", "value": value, "span": span}]

    def finish(self, frame: _Frame, span: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"kind": "raise", "exc": "StopIteration", "message": None, "span": span}]


class _CoroutineKind(_Kind):
    """`resume(self) -> int` (0 pending, 1 done).

    `await <coro>` stores the child frame in a local, polls it with
    `child.resume()`, and suspends with status 0 while the child is pending.
    Completion sets the frame's `result` field and returns 1.
    """

    marker = AWAIT_MARKER

    def methods(self, frame: _Frame, resume: dict[str, Any]) -> list[dict[str, Any]]:
        return [{**resume, "name": "resume", "returnType": _INT}]

    def suspend(self, frame: _Frame, value: dict[str, Any], span: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"kind": "return", "value": _const(PENDING, _INT, span), "span": span}]

    def finish(self, frame: _Frame, span: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"kind": "return", "value": _const(COMPLETE, _INT, span), "span": span}]


_KINDS: dict[str, _Kind] = {"generator": _GeneratorKind(), "coroutine": _CoroutineKind()}


class _Frame:
    """One resumable function: guard transform plus frame synthesis."""

    def __init__(
        self,
        fn: dict[str, Any],
        plan: FramePlan,
        kind: _Kind,
        plans: dict[str, FramePlan] | None = None,
    ) -> None:
        self.fn = fn
        self.plan = plan
        self.kind = kind
        self.plans = plans or {}
        self.span = fn["span"]
        self.frame_t = {"kind": "class", "name": plan.frame}
        self.locals = [dict(local) for local in fn["locals"]]
        self.local_names = {local["name"] for local in self.locals}
        self.temps: list[dict[str, Any]] = []
        self.points = 0

    # IR helpers bound to this frame
    def self_name(self, span: dict[str, Any]) -> dict[str, Any]:
        return _name(SELF, self.frame_t, span)

    def rs(self, span: dict[str, Any]) -> dict[str, Any]:
        return _name(RESUME, _INT, span)

    def fresh(self, span: dict[str, Any]) -> dict[str, Any]:
        """`rs == 0`: running forward, not re-entering toward a resume point."""
        return _compare("==", self.rs(span), _const(0, _INT, span), span)

    def field_get(self, name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        return {"kind": "attr", "value": self.self_name(span), "name": name, "type": typ, "span": span}

    def field_set(self, name: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        return {"kind": "setattr", "object": self.self_name(span), "name": name, "value": value, "span": span}

    def temp(self, hint: str, typ: dict[str, Any]) -> str:
        name = f".ps_coro_{hint}_{len(self.temps) + 1}"
        self.temps.append({"name": name, "type": typ})
        return name

    def copy_out(self, span: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "kind": "setattr",
                "object": self.self_name(span),
                "name": local["name"],
                "value": _name(local["name"], local["type"], span),
                "span": span,
            }
            for local in self.locals
        ]

    def copy_in(self, span: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            _assign(local["name"], self.field_get(local["name"], local["type"], span), span) for local in self.locals
        ]

    # build
    def build(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.fn.get("boxed") or self.fn.get("env"):
            raise CoroError("a resumable whose locals a closure captures is not lowered yet", self.span)
        span = self.span
        body = self._block(self.fn["body"])
        prologue = [
            _assign(RESUME, self.field_get(STATE, _INT, span), span),
            _if(_compare("==", self.rs(span), _const(DONE, _INT, span), span), self.kind.finish(self, span), [], span),
            self.field_set(STATE, _const(DONE, _INT, span), span),
            *self.copy_in(span),
        ]
        resume = {
            "params": [{"name": SELF, "type": self.frame_t}],
            "locals": [
                {"name": SELF, "type": self.frame_t},
                {"name": RESUME, "type": _INT},
                *self.locals,
                *self.temps,
            ],
            "body": [*prologue, *body, *self.kind.finish(self, span)],
            "span": span,
        }
        fields = [{"name": STATE, "type": _INT}]
        if self.plan.kind == "coroutine":
            # `.ps_task` is field 1 on every coroutine frame: the run loop in
            # ps_loop.c reads the resume cell at a fixed offset.
            fields.append({"name": TASK, "type": _RESUME_FUNC})
        fields.append({"name": RESULT, "type": self.plan.elem})
        if self.plan.send.get("kind") != "none":
            # The value a `send()` delivers into the generator.
            fields.append({"name": SEND, "type": self.plan.send})
        fields.extend(self.locals)
        frame = {
            "name": self.plan.frame,
            "fields": fields,
            "methods": self.kind.methods(self, resume),
            "span": span,
        }
        return self._wrapper(), frame, self._forwarder(resume)

    def _forwarder(self, resume: dict[str, Any]) -> dict[str, Any]:
        """`coro.<name>.resume(frame) -> int` as a module-level function.

        The run loop reaches the resume body through a callable cell (the
        `func` type is the only function-address value in the IR), and a
        closure cell can only name a module-level function. The frame class
        keeps its `resume` method; this forwarder just calls it.
        """
        if self.plan.kind != "coroutine":
            return None
        span = self.span
        self_arg = _name(SELF, self.frame_t, span)
        call = {
            "kind": "call",
            "callee": {"kind": "function", "name": f"cls.{self.plan.frame}.resume"},
            "args": [self_arg],
            "type": dict(_INT),
            "span": span,
        }
        return {
            "name": f"{self.plan.frame}.resume",
            "params": [{"name": SELF, "type": self.frame_t}],
            "returnType": dict(_INT),
            "locals": [{"name": SELF, "type": self.frame_t}],
            "body": [{"kind": "return", "value": call, "span": span}],
            "span": span,
        }

    def _wrapper(self) -> dict[str, Any]:
        span = self.span
        slot = ".ps_coro_frame"
        make = {
            "kind": "call",
            "callee": {"kind": "class", "name": self.plan.frame},
            "args": [],
            "type": self.frame_t,
            "span": span,
        }
        target = _name(slot, self.frame_t, span)
        body = [_assign(slot, make, span)]
        for param in self.fn["params"]:
            body.append(
                {
                    "kind": "setattr",
                    "object": target,
                    "name": param["name"],
                    "value": _name(param["name"], param["type"], span),
                    "span": span,
                }
            )
        if self.plan.kind == "coroutine":
            # The run loop reaches the resume body through this cell
            # (ps_loop.c reads field 1).
            body.append(
                {
                    "kind": "setattr",
                    "object": target,
                    "name": TASK,
                    "value": {
                        "kind": "closure",
                        "name": f"{self.plan.frame}.resume",
                        "env": [],
                        "type": dict(_RESUME_FUNC),
                        "span": span,
                    },
                    "span": span,
                }
            )
        body.append({"kind": "return", "value": target, "span": span})
        params_locals = [{"name": p["name"], "type": p["type"]} for p in self.fn["params"]]
        return {
            "name": self.fn["name"],
            "params": self.fn["params"],
            "returnType": self.frame_t,
            "locals": [*params_locals, {"name": slot, "type": self.frame_t}],
            "body": body,
            "span": span,
        }

    # guard transform
    def _block(self, stmts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Re-entrant form of a statement list.

        Suspend ids are numbered in preorder, so the ids under one statement
        form a contiguous range. On resume `rs` holds the target id: plain
        statements before a suspending one run only when `rs == 0`, and a
        suspending statement whose ids all lie below the target is skipped
        (`rs <= hi`). `rs` returns to 0 at the resume point itself, so the
        statements after the last suspending one need no guard.
        """
        suspending = [index for index, stmt in enumerate(stmts) if _suspends(stmt, self.kind.marker)]
        if not suspending:
            return [self._plain(stmt) for stmt in stmts]
        last = suspending[-1]
        out: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        for index, stmt in enumerate(stmts):
            if index not in suspending:
                if index < last:
                    pending.append(self._plain(stmt))
                else:
                    out.append(self._plain(stmt))
                continue
            span = stmt["span"]
            if pending:
                out.append(_if(self.fresh(span), pending, [], span))
                pending = []
            lowered = self._suspending(stmt)
            if index < last:
                out.append(_if(_compare("<=", self.rs(span), _const(self.points, _INT, span), span), lowered, [], span))
            else:
                out.extend(lowered)
        return out

    def _suspending(self, stmt: dict[str, Any]) -> list[dict[str, Any]]:
        span = stmt["span"]
        kind = stmt["kind"]
        if kind == "expr" and _is_marker(stmt, SLEEP_MARKER):
            return self._sleep(stmt, span)
        if kind == "expr" and _is_marker(stmt, self.kind.marker):
            if self.kind.marker == AWAIT_MARKER:
                return self._await(stmt, span)
            self.points += 1
            point = self.points
            value = stmt["expr"]["args"][0] if stmt["expr"]["args"] else _const(None, _NONE, span)
            leave = [
                *self.copy_out(span),
                self.field_set(STATE, _const(point, _INT, span), span),
                *self.kind.suspend(self, value, span),
            ]
            arrive = [_assign(RESUME, _const(0, _INT, span), span)]
            recv = stmt["expr"].get("recv")
            if recv is not None:
                # A yield used as an expression: the sent value (`.ps_send`,
                # stored by `send()`) lands in the recv temp the lowering
                # allocated for the marker's continuation.
                typ = stmt["expr"].get("recvType", dict(_NONE))
                arrive.append(_assign(recv, self.field_get(SEND, typ, span), span))
            return [_if(self.fresh(span), leave, arrive, span)]
        if kind == "if":
            taken = self.temp("if", _BOOL)
            then = self._block(stmt["then"])
            then_hi = self.points
            other = self._block(stmt["else"])
            choose = _if(
                self.fresh(span),
                [_assign(taken, stmt["cond"], span)],
                [_assign(taken, _compare("<=", self.rs(span), _const(then_hi, _INT, span), span), span)],
                span,
            )
            return [choose, _if(_name(taken, _BOOL, span), then, other, span)]
        if kind == "while":
            again = self.temp("wh", _BOOL)
            body = self._block(stmt["body"])
            enter = _if(
                self.fresh(span),
                [_assign(again, stmt["cond"], span)],
                [_assign(again, _const(True, _BOOL, span), span)],
                span,
            )
            loop = {
                "kind": "while",
                "cond": _name(again, _BOOL, span),
                "body": [*body, _assign(again, stmt["cond"], span)],
                "span": span,
            }
            return [enter, loop]
        raise CoroError("a suspension inside try or with is not lowered yet", span)

    def _await(self, stmt: dict[str, Any], span: dict[str, Any]) -> list[dict[str, Any]]:
        """Expand `__ps_await__(child)` into the child-poll suspension.

        The child frame is in the local the lowerer bound (the marker's
        argument names it). The parent polls `child.resume()`; while the
        child reports pending the parent suspends with status 0, setting the
        resume point to this await so the next `resume()` re-polls the same
        child. When the child completes, its `.ps_result` field carries the
        awaited value into the lowerer's result temp.
        """
        self.points += 1
        point = self.points
        child = stmt["expr"]["args"][0]
        child_t = child["type"]
        child_frame = child_t["name"]
        slot = child["id"]
        result = _await_result_name(slot)
        done = self.temp("awd", _BOOL)
        status = self.temp("aws", _INT)
        call = {
            "kind": "call",
            "callee": {"kind": "function", "name": f"cls.{child_frame}.resume"},
            "args": [_name(slot, child_t, span)],
            "type": dict(_INT),
            "span": span,
        }
        read = _attr_expr(_name(slot, child_t, span), RESULT, self._await_result_type(child_frame), span)
        pending = [
            _assign(done, _compare("==", _name(status, _INT, span), _const(COMPLETE, _INT, span), span), span),
            _if(
                _name(done, _BOOL, span),
                [],
                [
                    {
                        "kind": "expr",
                        "expr": {
                            "kind": "call",
                            "callee": {"kind": "module", "module": "asyncio", "name": "wait"},
                            "args": [_name(slot, child_t, span), self.self_name(span)],
                            "type": dict(_NONE),
                            "span": span,
                            "symbol": "ps_loop_wait",
                        },
                        "span": span,
                    },
                    _if(
                        _const(True, _BOOL, span),
                        self.kind.suspend(self, _const(0, _INT, span), span),
                        [],
                        span,
                    ),
                ],
                span,
            ),
        ]
        # Fresh entry: copy out, mark the point, poll. Re-entry: re-poll the
        # same child (state still names this point, so a second suspension
        # comes back here). Both paths read the result only once the child
        # reports done.
        leave = [
            *self.copy_out(span),
            self.field_set(STATE, _const(point, _INT, span), span),
            _assign(status, call, span),
            *pending,
        ]
        arrive = [
            _assign(RESUME, _const(0, _INT, span), span),
            _assign(status, call, span),
            *pending,
        ]
        result_set = [_assign(result, read, span)] if result is not None else []
        return [
            _if(self.fresh(span), leave, arrive, span),
            *result_set,
        ]

    def _await_result_type(self, child_frame: str) -> dict[str, Any]:
        for plan in self.plans.values():
            if plan.frame == child_frame:
                return plan.elem
        raise CoroError(f"await of an unknown coroutine frame {child_frame}", self.span)

    def _sleep(self, stmt: dict[str, Any], span: dict[str, Any]) -> list[dict[str, Any]]:
        """Expand `__ps_sleep__(delay)` into arm-then-suspend.

        The timer table is keyed by frame; the loop re-queues this frame when
        the deadline passes and the resume enters at this point again (the
        state field below), falling straight through the armed call.
        """
        self.points += 1
        point = self.points
        delay = stmt["expr"]["args"][0]
        arm = {
            "kind": "call",
            "callee": {"kind": "module", "module": "asyncio", "name": "sleep"},
            "args": [_name(SELF, self.frame_t, span), delay],
            "type": dict(_NONE),
            "span": span,
            "symbol": "ps_loop_sleep",
        }
        leave = [
            *self.copy_out(span),
            self.field_set(STATE, _const(point, _INT, span), span),
            {"kind": "expr", "expr": arm, "span": span},
            *self.kind.suspend(self, _const(0, _INT, span), span),
        ]
        arrive = [_assign(RESUME, _const(0, _INT, span), span)]
        return [_if(self.fresh(span), leave, arrive, span)]

    def _plain(self, stmt: dict[str, Any]) -> dict[str, Any]:
        """A statement without suspensions: only `return` changes meaning."""
        kind = stmt["kind"]
        if kind == "return":
            value = stmt.get("value")
            if _is_return_marker(value):
                # `return v` in a coroutine: the frame's result field carries
                # v. Wrapped in an always-true if so the caller keeps a single
                # statement per input.
                result = value["args"][0]
                leave = [
                    self.field_set(RESULT, result, stmt["span"]),
                    {"kind": "return", "value": _const(COMPLETE, _INT, stmt["span"]), "span": stmt["span"]},
                ]
                return _if(_const(True, _BOOL, stmt["span"]), leave, [], stmt["span"])
            finish = self.kind.finish(self, stmt["span"])
            return finish[0] if len(finish) == 1 else _if(_const(True, _BOOL, stmt["span"]), finish, [], stmt["span"])
        if kind == "if":
            return {
                **stmt,
                "then": [self._plain(s) for s in stmt["then"]],
                "else": [self._plain(s) for s in stmt["else"]],
            }
        if kind == "while":
            return {**stmt, "body": [self._plain(s) for s in stmt["body"]]}
        if kind == "try" and _contains_kind(stmt, "return"):
            raise CoroError("return inside try or with in a resumable is not lowered yet", stmt["span"])
        return stmt


def _suspends(stmt: dict[str, Any], marker: str) -> bool:
    if stmt["kind"] == "expr":
        return _is_marker(stmt, marker) or _is_marker(stmt, SLEEP_MARKER)
    if stmt["kind"] == "if":
        return any(_suspends(s, marker) for s in stmt["then"]) or any(_suspends(s, marker) for s in stmt["else"])
    if stmt["kind"] == "while":
        return any(_suspends(s, marker) for s in stmt["body"])
    if stmt["kind"] == "try":
        blocks = [stmt["body"], stmt.get("orelse", []), stmt.get("finalbody", [])]
        blocks += [handler["body"] for handler in stmt["handlers"]]
        return any(_suspends(s, marker) for block in blocks for s in block)
    return False


def _is_marker(stmt: dict[str, Any], marker: str) -> bool:
    expr = stmt.get("expr", {})
    callee = expr.get("callee", {}) if expr.get("kind") == "call" else {}
    return callee.get("kind") == "builtin" and callee.get("name") == marker


def _is_return_marker(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("kind") != "call":
        return False
    callee = value.get("callee", {})
    return callee.get("kind") == "builtin" and callee.get("name") == RETURN_MARKER


def _contains_kind(stmt: dict[str, Any], kind: str) -> bool:
    if stmt["kind"] == kind:
        return True
    blocks: list[list[dict[str, Any]]] = []
    for key in ("then", "else", "body", "orelse", "finalbody"):
        if isinstance(stmt.get(key), list):
            blocks.append(stmt[key])
    blocks += [handler["body"] for handler in stmt.get("handlers", [])]
    return any(_contains_kind(s, kind) for block in blocks for s in block)


def _name(name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "name", "id": name, "type": typ, "span": span}


def _const(value: Any, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    if typ["kind"] == "int":
        value = str(value)
    return {"kind": "const", "value": value, "type": typ, "span": span}


def _assign(target: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "assign", "target": target, "value": value, "span": span}


def _compare(op: str, left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "compare", "op": op, "left": left, "right": right, "type": _BOOL, "span": span}


def _attr_expr(value: dict[str, Any], name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "attr", "value": value, "name": name, "type": typ, "span": span}


def _await_result_name(slot: str) -> str:
    """The local that carries the awaited value out of the await.

    The lowerer binds the child frame in `slot` (`.ps_aw_N`) and the awaited
    value in the sibling name the IR pass derives from it, so the IR needs
    no extra key on the marker.
    """
    return AWAIT_RESULT_PREFIX + slot.split("_")[-1]


def _if(
    cond: dict[str, Any], then: list[dict[str, Any]], other: list[dict[str, Any]], span: dict[str, Any]
) -> dict[str, Any]:
    return {"kind": "if", "cond": cond, "then": then, "else": other, "span": span}
