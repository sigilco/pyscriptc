"""Resumable functions: generators now, `async def` next.

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

Async plugs in as a second `_Kind`: the marker is `__ps_await__`, the resume
method is `resume(self) -> int` (0 pending, 1 done) and the frame carries a
`result` field; the guard transform is shared.

Scope today (everything else keeps a fence with a precise message): module
generators annotated `Iterator[T]`, `Iterable[T]` or `Generator[T, None, None]`,
`yield v` and `yield from <iterable>` as statements, bare `return`, yields
under `if`/`while`/`for`. Not yet: yield inside try/with, `send`/`throw`/
`close`, nested or generic or decorated generators, methods, package units.
`PYSC_CORO=0` turns the lowering off (generators fence as before).
"""

from __future__ import annotations

import ast
import copy
import os
from dataclasses import dataclass, field
from typing import Any

from pyscriptc.errors import span_of

YIELD_MARKER = "__ps_yield__"
STATE = ".ps_state"
SELF = ".ps_self"
RESUME = ".ps_rs"
DONE = -1

_INT = {"kind": "int"}
_BOOL = {"kind": "bool"}
_NONE = {"kind": "none"}
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

    kind: str  # "generator" (async: "coroutine")
    function: str
    frame: str
    elem: dict[str, Any]
    params: list[dict[str, Any]] = field(default_factory=list)


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


def _shape_error(fn: ast.FunctionDef) -> tuple[ast.AST, str] | None:
    """The first body construct outside today's generator scope, or None."""

    def visit(stmts: list[ast.stmt], in_try: bool) -> tuple[ast.AST, str] | None:
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return stmt, "a nested def or class in a generator is not lowered yet"
            if isinstance(stmt, ast.Return):
                if stmt.value is not None and not (isinstance(stmt.value, ast.Constant) and stmt.value.value is None):
                    return stmt, "return with a value in a generator is not lowered yet"
                if in_try:
                    return stmt, "return inside try or with in a generator is not lowered yet"
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, (ast.Yield, ast.YieldFrom)):
                if in_try:
                    return stmt, "yield inside try or with is not lowered yet"
                if isinstance(stmt.value, ast.Yield) and stmt.value.value is None:
                    return stmt, "bare yield is not lowered yet"
                inner = [stmt.value.value] if stmt.value.value is not None else []
            else:
                inner = [child for child in ast.iter_child_nodes(stmt) if isinstance(child, ast.expr)]
            for expr in inner:
                for node in ast.walk(expr):
                    if isinstance(node, (ast.Yield, ast.YieldFrom, ast.Await)):
                        return node, "yield as an expression (send) is not lowered yet"
                    if isinstance(node, ast.Lambda):
                        return node, "a lambda in a generator is not lowered yet"
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

    def _coro_signature(self, fn: ast.FunctionDef) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
        """The outward signature of a module-level generator, or None for any
        other function (the caller then uses `_signature` as before)."""
        if not enabled() or self.module_name or self.cataloguing or fn.decorator_list:
            return None
        if not isinstance(fn, ast.FunctionDef) or not is_generator(fn):
            return None
        elem = self._coro_elem(fn)
        shape = _shape_error(fn)
        if shape is not None:
            self.fail(shape[0], "PS2002", shape[1])
        plain = copy.copy(fn)
        plain.returns = ast.Constant(value=None)
        params, _ = self._signature(plain, None, "method")
        from pyscriptc.lower._core import _has_typevar

        if _has_typevar(elem) or any(_has_typevar(param["type"]) for param in params):
            self.fail(fn, "PS2002", "a generic generator is not lowered yet")
        frame = f"gen.{fn.name}"
        frame_t = {"kind": "class", "name": frame}
        self_t = [{"name": "self", "type": frame_t}]
        self.classes[frame] = {
            "fields": [],
            "own_fields": [],
            "methods": {"__iter__": (self_t, frame_t), "__next__": (self_t, elem)},
            "method_kind": {},
            "method_owner": {},
            "base": None,
            "coro": "generator",
        }
        self.known_classes.add(frame)
        self._coro_plans()[fn.name] = FramePlan("generator", fn.name, frame, elem, params)
        return params, frame_t

    def _coro_elem(self, fn: ast.FunctionDef) -> dict[str, Any]:
        ann = fn.returns
        if ann is None:
            self.fail(fn, "PS1001", f"{fn.name} needs a return annotation")
        form = self._typing_alias(ann.value) if isinstance(ann, ast.Subscript) else None
        if form not in _GENERATOR_FORMS:
            self.fail(ann, "PS1001", "a generator is annotated Iterator[T], Iterable[T] or Generator[T, None, None]")
        args = list(ann.slice.elts) if isinstance(ann.slice, ast.Tuple) else [ann.slice]
        if form == "Generator":
            if len(args) not in {1, 3} or any(
                not (isinstance(arg, ast.Constant) and arg.value is None) for arg in args[1:]
            ):
                self.fail(ann, "PS2002", "Generator[T, S, R] with send or return types is not lowered yet")
        elif len(args) != 1:
            self.fail(ann, "PS1001", f"{form}[T] takes one type")
        return self._annotation(args[0])

    def _coro_body_sig(self, name: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Signature the body lowers under: a generator body returns None."""
        plan = self._coro_plans().get(name)
        self.__dict__["_coro_active"] = plan
        params, ret = self.sigs[name]
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
            if isinstance(stmt, ast.Return):
                span = span_of(stmt, self.filename)
                return [{"kind": "return", "value": _const(None, _NONE, span), "span": span}]
        return super()._stmt_core(stmt)

    def _coro_yield(self, stmt: ast.Expr, node: ast.Yield, plan: FramePlan) -> list[dict[str, Any]]:
        span = span_of(stmt, self.filename)
        assert node.value is not None
        value = self._expr(node.value, plan.elem)
        self._expect(node, value["type"], plan.elem, "yield")
        call = {
            "kind": "call",
            "callee": {"kind": "builtin", "name": YIELD_MARKER},
            "args": [value],
            "type": dict(_NONE),
            "span": span,
        }
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
        plan = plans.get(fn["name"])
        if plan is None:
            functions.append(fn)
            continue
        try:
            wrapper, frame = _Frame(fn, plan, _KINDS[plan.kind]).build()
        except CoroError as exc:
            fences.append({"code": "PS2002", "message": exc.message, "function": fn["name"], "span": exc.span})
            continue
        functions.append(wrapper)
        classes.append(frame)
    return {**module, "functions": functions, "classes": classes, "fences": fences}


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
    (code 8), which `_for_dunder` turns into the loop exit."""

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
        return [iter_m, next_m]

    def suspend(self, frame: _Frame, value: dict[str, Any], span: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"kind": "return", "value": value, "span": span}]

    def finish(self, frame: _Frame, span: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"kind": "raise", "exc": "StopIteration", "message": None, "span": span}]


_KINDS: dict[str, _Kind] = {"generator": _GeneratorKind()}


class _Frame:
    """One resumable function: guard transform plus frame synthesis."""

    def __init__(self, fn: dict[str, Any], plan: FramePlan, kind: _Kind) -> None:
        self.fn = fn
        self.plan = plan
        self.kind = kind
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
        return [self.field_set(local["name"], _name(local["name"], local["type"], span), span) for local in self.locals]

    def copy_in(self, span: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            _assign(local["name"], self.field_get(local["name"], local["type"], span), span) for local in self.locals
        ]

    # build
    def build(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.fn.get("boxed") or self.fn.get("env"):
            raise CoroError("a generator whose locals a closure captures is not lowered yet", self.span)
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
        frame = {
            "name": self.plan.frame,
            "fields": [{"name": STATE, "type": _INT}, *self.locals],
            "methods": self.kind.methods(self, resume),
            "span": span,
        }
        return self._wrapper(), frame

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
        if kind == "expr" and _is_marker(stmt, self.kind.marker):
            self.points += 1
            point = self.points
            value = stmt["expr"]["args"][0] if stmt["expr"]["args"] else _const(None, _NONE, span)
            leave = [
                *self.copy_out(span),
                self.field_set(STATE, _const(point, _INT, span), span),
                *self.kind.suspend(self, value, span),
            ]
            arrive = [_assign(RESUME, _const(0, _INT, span), span)]
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

    def _plain(self, stmt: dict[str, Any]) -> dict[str, Any]:
        """A statement without suspensions: only `return` changes meaning."""
        kind = stmt["kind"]
        if kind == "return":
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
            raise CoroError("return inside try or with in a generator is not lowered yet", stmt["span"])
        return stmt


def _suspends(stmt: dict[str, Any], marker: str) -> bool:
    if stmt["kind"] == "expr":
        return _is_marker(stmt, marker)
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


def _if(
    cond: dict[str, Any], then: list[dict[str, Any]], other: list[dict[str, Any]], span: dict[str, Any]
) -> dict[str, Any]:
    return {"kind": "if", "cond": cond, "then": then, "else": other, "span": span}
