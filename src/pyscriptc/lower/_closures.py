"""Nested definitions, lambdas and callable values (R13/R22 pack 1).

A callable value is an opaque cell holding an implementation entry point and
an environment pointer; the env stores pointers into the parent's stack
slots, so `nonlocal` writes and transitive captures share one cell.
"""

import ast
from typing import Any

from pyscriptc.errors import span_of
from pyscriptc.ir import types_equal
from pyscriptc.lower._core import Fence, _assign, _name
from pyscriptc.lower._stmt import _always_returns

# Lowerer fields swapped for the duration of one nested body's lowering.
_FN_STATE = (
    "locals",
    "captures",
    "function",
    "fn_globals",
    "fn_nonlocals",
    "fn_body",
    "frozen_names",
    "dead_names",
    "exc_stack",
    "prelude",
    "break_flag",
    "continue_flag",
    "broke_flag",
    "bindings",
    "type_aliases",
    "declared",
    "boxed_names",
)


class ClosuresMixin:
    def _nested_def(self, fn: ast.FunctionDef | ast.AsyncFunctionDef, span: dict[str, Any]) -> list[dict[str, Any]]:
        if isinstance(fn, ast.AsyncFunctionDef):
            self.fail(fn, "PS2002", "async functions are not lowered")
        params, ret = self._signature(fn, None, "static")
        ftype = {"kind": "func", "params": [param["type"] for param in params], "return": ret}
        # Bind the name before building the env so the def can capture itself
        # (self-recursion reads its own stack slot through the env pointer).
        self.locals[fn.name] = ftype
        built = self._build_callable(fn.name, params, ret, fn.body, fn)
        if built is None:
            return [{"kind": "pass", "span": span}]
        name, env, _ = built
        stmts = [_assign(fn.name, {"kind": "closure", "name": name, "env": env, "type": ftype, "span": span}, span)]
        stmts.extend(self._decorators(fn, span))
        return stmts

    def _decorators(self, fn: ast.FunctionDef | ast.AsyncFunctionDef, span: dict[str, Any]) -> list[dict[str, Any]]:
        """`@dec` on a nested def rewrites the just-bound name to `name =
        dec(name)`; the local's type updates to the decorator's return."""
        if not fn.decorator_list:
            return []
        if len(fn.decorator_list) != 1:
            self.fail(fn, "PS2002", "stacked decorators are not lowered")
        dec = fn.decorator_list[0]
        value = self._apply_decorator(dec, _name(fn.name, self.locals[fn.name], span), span)
        self.locals[fn.name] = value["type"]
        return [_assign(fn.name, value, span)]

    def _apply_decorator(self, dec: ast.expr, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        """`dec(value)` as a callable-value call; the result must stay callable."""
        dec_expr: dict[str, Any] | None = None
        try:
            dec_expr = self._expr(dec)
        except Fence:
            pass
        params = dec_expr["type"]["params"] if dec_expr is not None else []
        if (
            dec_expr is None
            or dec_expr["type"]["kind"] != "func"
            or len(params) != 1
            or not types_equal(params[0], value["type"])
            or dec_expr["type"]["return"]["kind"] != "func"
        ):
            self.fail(dec, "PS2002", "user decorators are not lowered")
        return {
            "kind": "call",
            "callee": {"kind": "value", "target": dec_expr},
            "args": [value],
            "type": dec_expr["type"]["return"],
            "span": span,
        }

    def _partial_callable(self, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
        """`functools.partial(f, a, ...)`: a callable cell whose env holds the
        bound arguments and the target pair; a synthesized env-first stub
        prepends them at each call. Any failure falls back to the standing
        `functools.partial is not lowered` fence."""
        try:
            return self._partial_callable_inner(node, span)
        except Fence:
            self.fail(node, "PS2001", "functools.partial is not lowered")

    def _partial_callable_inner(self, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
        if node.keywords:
            self.fail(node, "PS2001", "functools.partial is not lowered")
        if not node.args:
            self.fail(node, "PS2001", "functools.partial is not lowered")
        target = self._expr(node.args[0])
        ftype = target["type"]
        if ftype["kind"] != "func":
            self.fail(node, "PS2001", "functools.partial is not lowered")
        bound = list(node.args[1:])
        if len(bound) > len(ftype["params"]):
            self.fail(node, "PS2001", "functools.partial is not lowered")
        env: list[dict[str, Any]] = []
        for index, arg in enumerate(bound):
            expect = ftype["params"][index]
            value = self._expr(arg, expect)
            self._expect(arg, value["type"], expect, f"functools.partial argument {index + 2}")
            env.append({"init": value, "type": expect})
        env.append({"init": target, "type": ftype})
        result = {"kind": "func", "params": ftype["params"][len(bound) :], "return": ftype["return"]}
        return {
            "kind": "closure",
            "name": "pa.partial",
            "env": env,
            "type": result,
            "stub": {
                "bound": [item["type"] for item in env[:-1]],
                "params": result["params"],
                "return": result["return"],
            },
            "span": span,
        }

    def _callable_name_type(self, name: str) -> dict[str, Any] | None:
        """The func type a bare name carries, from any scope, else None."""
        if name in self.sigs and name not in self.generic_fns:
            params, ret = self.sigs[name]
            return {"kind": "func", "params": [param["type"] for param in params], "return": ret}
        ftype = self.locals.get(name, self.captures.get(name))
        if ftype is not None and ftype["kind"] == "func":
            return ftype
        slot = self.global_slots.get(name)
        if slot is not None and slot.get("active") and slot["type"]["kind"] == "func":
            return slot["type"]
        return None

    def _nonlocal(self, stmt: ast.Nonlocal, span: dict[str, Any]) -> list[dict[str, Any]]:
        for name in stmt.names:
            if name not in self.captures:
                self.fail(stmt, "PS2002", f"nonlocal {name} has no enclosing binding")
        return [{"kind": "pass", "span": span}]

    def _lambda(self, node: ast.Lambda, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
        """`f: Callable[[int], int] = lambda x: x + 1`. Parameter types come from
        the annotation; the body is a single return."""
        if expect is None or expect["kind"] != "func":
            self.fail(node, "PS1001", "a lambda needs a Callable[[args], ret] annotation")
        args = node.args
        if args.vararg or args.kwarg or args.kwonlyargs or args.defaults or args.posonlyargs:
            self.fail(node, "PS2002", "lambda arguments beyond plain parameters are not lowered")
        if len(args.args) != len(expect["params"]):
            self.fail(node, "PS1003", "the lambda does not match its Callable annotation")
        params = [{"name": arg.arg, "type": typ} for arg, typ in zip(args.args, expect["params"], strict=True)]
        ret_stmt = ast.Return(value=node.body)
        ast.copy_location(ret_stmt, node.body)
        built = self._build_callable("lam", params, expect["return"], [ret_stmt], node)
        if built is None:
            self.fail(node, "PS2002", "the lambda body is not lowered")
        name, env, _ = built
        return {"kind": "closure", "name": name, "env": env, "type": expect, "span": span}

    def _build_callable(
        self,
        name: str,
        params: list[dict[str, Any]],
        ret: dict[str, Any],
        body: list[ast.stmt],
        fn: ast.AST,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]] | None:
        """Lower one nested body into an env-first IR function.

        Captures are the free names the subtree needs from enclosing scopes
        (including names a deeper `nonlocal` resolves through this frame). Each
        env entry binds a parent stack slot by pointer. Returns None when the
        body already collected fences, like a fenced top-level function.
        """
        qname = self._nested_name(name)
        env: list[dict[str, Any]] = []
        for free in self._free_names(body):
            typ = self.locals.get(free, self.captures.get(free))
            if typ is not None:
                env.append({"name": free, "type": typ})
                if free in self.locals:
                    # The env stores a pointer to this local, so a cell handed
                    # to a closure that outlives the frame must be heap
                    # storage, not an alloca.
                    self.boxed_names.add(free)
        ftype = {"kind": "func", "params": [param["type"] for param in params], "return": ret}
        saved = {field: getattr(self, field) for field in _FN_STATE}
        self.locals = {param["name"]: param["type"] for param in params}
        self.captures = {item["name"]: item["type"] for item in env}
        self.function = qname
        self.fn_globals = self._globals_for(list(body))
        # Nonlocal may sit inside an if/while; nested defs keep their own set.
        self.fn_nonlocals = set()
        stack: list[ast.AST] = list(body)
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(node, ast.Nonlocal):
                self.fn_nonlocals.update(node.names)
            else:
                stack.extend(ast.iter_child_nodes(node))
        self.fn_body = list(body)
        self.frozen_names = set()
        self.dead_names = set()
        self.exc_stack = []
        self.prelude = []
        self.break_flag = None
        self.continue_flag = None
        self.broke_flag = None
        self.bindings = dict(self.bindings)
        self.type_aliases = dict(self.type_aliases)
        self.declared = set(self.declared)
        self.sigs[qname] = (params, ret)
        mark = len(self.caught)
        try:
            lowered = self._body(list(body))
            if len(self.caught) == mark and ret["kind"] != "none" and not _always_returns(lowered):
                self.fail(fn, "PS1004", f"{name} can fall off the end and return None")
            if len(self.caught) == mark:
                record = {
                    "name": qname,
                    "env": env,
                    "params": params,
                    "returnType": ret,
                    "locals": [{"name": n, "type": t} for n, t in self.locals.items()],
                    "body": lowered,
                    "span": span_of(fn, self.filename),
                }
                if self.boxed_names:
                    record["boxed"] = sorted(self.boxed_names)
                self.nested_fns.append(record)
        except Fence as exc:
            self.caught.append(exc)
        finally:
            for field, value in saved.items():
                setattr(self, field, value)
        return (qname, env, ftype) if len(self.caught) == mark else None

    def _nested_name(self, name: str) -> str:
        base = f"{self.function}.{name}"
        qname = base
        while qname in self.sigs:
            self.nested_n += 1
            qname = f"{base}_{self.nested_n}"
        return qname

    def _free_names(self, body: list[ast.stmt]) -> list[str]:
        """Names a nested body's whole subtree needs from enclosing scopes.

        Loads minus names bound inside, plus `nonlocal` declarations and the
        propagated needs of deeper nested defs (which re-export through this
        frame). In first-use order so env layout is stable.
        """
        need: dict[str, None] = {}
        own: set[str] = set()
        globs: set[str] = set()
        nonlocals: set[str] = set()
        subs: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        stack: list[ast.AST] = list(body)
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                own.add(node.name)
                subs.append(node)
                continue
            if isinstance(node, ast.Global):
                globs.update(node.names)
                continue
            if isinstance(node, ast.Nonlocal):
                nonlocals.update(node.names)
                continue
            if isinstance(node, ast.Lambda):
                args = node.args
                bound = {arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
                bound.update(arg.arg for arg in (args.vararg, args.kwarg) if arg)
                for ref in ast.walk(node.body):
                    if isinstance(ref, ast.Name) and isinstance(ref.ctx, ast.Load) and ref.id not in bound:
                        need.setdefault(ref.id)
                continue
            if isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Load):
                    need.setdefault(node.id)
                else:
                    own.add(node.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                own.add(node.name)
            stack.extend(reversed(list(ast.iter_child_nodes(node))))
        # `nonlocal`/`global` stores do not bind here; their names stay free.
        own -= nonlocals | globs
        for name in nonlocals:
            need.setdefault(name)
        for sub in subs:
            for free in self._free_names(sub.body):
                if free not in own and free not in globs:
                    need.setdefault(free)
        return [name for name in need if name not in own and name not in globs]
