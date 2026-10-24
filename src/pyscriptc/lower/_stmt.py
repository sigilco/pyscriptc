"""Statement lowering for the lowerer.

Split out of `__init__.py` as E0 move 3. `Lowerer` composes this mixin in.
The area owns the `_stmt` dispatch and its statement forms: assignments
(plain, annotated, augmented, global slots), `if`/`while`/`for` (range,
repeat, chain, enumerate, zip, list), `break`/`continue` plumbing,
`return`/`raise`/`assert`/`del`, `try` handlers, `with` (suppress,
nullcontext, `open`, `importlib.resources.as_file`), and `match` on
literal, wildcard, capture, or, guarded, and `None` patterns.

Loop-flag plumbing and close injection are statement-shaped and moved with
the area. Small leaf helpers shared across the package (tables, `_assign`,
scalar constructors) stay in `_core.py`; the boundary calls in both
directions stay `self._method()` so the move is behavior-identical.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

from pyscriptc.builtins import EXCEPTIONS, RESERVED_EXCEPTIONS
from pyscriptc.errors import span_of
from pyscriptc.ffi_manifest import is_handle_type
from pyscriptc.ir import types_equal
from pyscriptc.lower._core import (
    _SCALAR,
    Fence,
    _assign,
    _attr,
    _binary_int,
    _compare,
    _concat_str,
    _const_bool,
    _const_int,
    _const_none,
    _const_str,
    _has_typevar,
    _is_none_expr,
    _item,
    _len_call,
    _name,
    _raise_value,
    _show,
    _tuple_assign,
    _zero,
)
from pyscriptc.restlower import alias_name

if TYPE_CHECKING:
    from pyscriptc.lower import Lowerer


class StmtMixin:
    def _loop_flag(self, body: list[ast.stmt]) -> str | None:
        if not any(_stmt_breaks_this_loop(stmt) for stmt in body):
            return None
        flag = self._temp("brk")
        self.locals[flag] = {"kind": "bool"}
        return flag

    def _continue_loop_flag(self, body: list[ast.stmt]) -> str | None:
        if not any(_stmt_continues_this_loop(stmt) for stmt in body):
            return None
        flag = self._temp("cont")
        self.locals[flag] = {"kind": "bool"}
        return flag

    def _continue_reset(self, span: dict[str, Any]) -> list[dict[str, Any]]:
        if self.continue_flag is None:
            return []
        return [_assign(self.continue_flag, _const_bool(False, span), span)]

    def _skip_rest(self, stmts: list[dict[str, Any]], span: dict[str, Any]) -> dict[str, Any]:
        """Skip the rest of this loop body after `break` or `continue`.

        `break` and `continue` each have a flag. The for-loop step stays outside
        this guard, so `continue` still advances and `break` does not.
        """
        guarded = stmts
        if self.continue_flag is not None:
            guarded = [
                {"kind": "if", "cond": _not_bool(self.continue_flag, span), "then": guarded, "else": [], "span": span}
            ]
        if self.break_flag is not None:
            guarded = [
                {"kind": "if", "cond": _not_bool(self.break_flag, span), "then": guarded, "else": [], "span": span}
            ]
        return guarded[0]

    def _break_init(self, span: dict[str, Any]) -> list[dict[str, Any]]:
        if self.break_flag is None:
            return []
        return [_assign(self.break_flag, _const_bool(False, span), span)]

    def _skip_on_break(self, stmts: list[dict[str, Any]], span: dict[str, Any]) -> list[dict[str, Any]]:
        if self.break_flag is None:
            return stmts
        return [{"kind": "if", "cond": _not_bool(self.break_flag, span), "then": stmts, "else": [], "span": span}]

    def _user_while(
        self,
        cond: dict[str, Any],
        body: list[dict[str, Any]],
        span: dict[str, Any],
        *,
        init: bool = True,
        cond_pre: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """`while cond: body`, and `break` sets a flag the existing while already tests.

        The flag starts false. The loop runs while it is false and `cond` is
        true. `break` sets the flag and the rest of that body is an `if`.
        A false `cond` sets the flag too, so the condition is not tested again.
        Statements that compute `cond` run at the start of each iteration.
        """
        flag = self.break_flag
        pre = cond_pre or []
        if flag is None and not pre:
            return [{"kind": "while", "cond": cond, "body": body, "span": span}]
        own_flag = False
        if flag is None:
            flag = self._temp("whl")
            self.locals[flag] = {"kind": "bool"}
            own_flag = True
        loop = {
            "kind": "while",
            "cond": _not_bool(flag, span),
            "body": [
                *pre,
                {
                    "kind": "if",
                    "cond": cond,
                    "then": body,
                    "else": [_assign(flag, _const_bool(True, span), span)],
                    "span": span,
                },
            ],
            "span": span,
        }
        if not init and not own_flag:
            return [loop]
        return [_assign(flag, _const_bool(False, span), span), loop]

    def _stmt(self, stmt: ast.stmt) -> list[dict[str, Any]]:
        saved = self.prelude
        self.prelude = []
        try:
            produced = self._stmt_core(stmt)
        finally:
            pre = self.prelude
            self.prelude = saved
        return [*pre, *produced]

    def _stmt_core(self, stmt: ast.stmt) -> list[dict[str, Any]]:
        span = span_of(stmt, self.filename)
        if isinstance(stmt, ast.Pass):
            return [{"kind": "pass", "span": span}]
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Attribute):
            return [self._setattr(stmt, span)]
        if _is_setitem(stmt):
            return [self._setitem(stmt, span)]
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return self._nested_def(stmt, span)
        if isinstance(stmt, ast.Nonlocal):
            return self._nonlocal(stmt, span)
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            if stmt.target.id in self.fn_nonlocals:
                self.fail(stmt, "PS2002", f"nonlocal {stmt.target.id} cannot be annotated")
            typ = self._annotation(stmt.annotation)
            if _has_typevar(typ):
                self.fail(stmt, "PS1001", f"{stmt.target.id} needs a concrete type")
            if stmt.value is None:
                self.locals[stmt.target.id] = typ
                return [_assign(stmt.target.id, _zero(typ, span), span)]
            try:
                value = self._expr(stmt.value, typ)
            except Fence:
                # A value that fences must not unbind the name: the fence
                # aborts the statement, and a later read of the name would
                # raise a second, misleading PS2002 on top of the real
                # fence. Bind the annotation's type so the name resolves,
                # and re-raise the real fence.
                if not (typ.get("kind") == "class" and typ.get("name") == "Deque" and "elem" not in typ):
                    # A bare `collections.deque` annotation carries no element
                    # type; binding it would send a later `.append` into the
                    # synthetic-method path with no `elem` to check against.
                    self.locals[stmt.target.id] = typ
                raise
            self._expect(stmt, value["type"], typ, stmt.target.id)
            if self._narrows_to_generator(typ, value["type"]):
                # An Iterator[T]/Generator[T, None, None] local holding a
                # generator keeps the frame type: every later use (next,
                # send, for) dispatches on the frame's real shape, not the
                # synthetic cursor.
                typ = value["type"]
            self.locals[stmt.target.id] = typ
            # A re-annotated assignment rebinds the handle: the dropped
            # value is freed first (issue #224).
            dropped = self._rebind_handle(stmt.target.id, typ, stmt.value, span)
            self._note_frozen(stmt.target.id, stmt.value)
            return [*dropped, _assign(stmt.target.id, value, span)]
        if isinstance(stmt, ast.Global):
            for name in stmt.names:
                slot = self.global_slots.get(name)
                if slot is None or not slot.get("active"):
                    self.fail(stmt, "PS2002", f"global {name} is not a module slot")
            return [{"kind": "pass", "span": span}]
        if isinstance(stmt, ast.Assign):
            if (
                len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and (nt_call := self._namedtuple_call(stmt.value)) is not None
            ):
                self._namedtuple_decl(stmt.targets[0].id, nt_call, stmt)
                return [{"kind": "pass", "span": span}]
            if (
                len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and (tv_call := self._typevar_call(stmt.value)) is not None
            ):
                self._declare_typevar(stmt.targets[0].id, tv_call, stmt)
                return [{"kind": "pass", "span": span}]
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                return self._spread_assign(stmt, span)
            target = stmt.targets[0].id
            if target in self.fn_globals or (self.function == self.entry_fn_name and target in self.global_slots):
                return self._assign_global(stmt, target, span)
            if target in self.fn_nonlocals and target in self.captures:
                value = self._expr(stmt.value, self.captures[target])
                self._expect(stmt, value["type"], self.captures[target], target)
                return [_assign(target, value, span)]
            if target not in self.locals:
                if isinstance(stmt.value, ast.Name) and stmt.value.id != target:
                    # `h = f`: a function value rebinds like a Callable
                    # annotation; the variable becomes callable-typed.
                    ftype = self._callable_name_type(stmt.value.id)
                    if ftype is not None:
                        self.locals[target] = ftype
                        value = self._expr(stmt.value, ftype)
                        self._expect(stmt, value["type"], ftype, target)
                        return [_assign(target, value, span)]
                if isinstance(stmt.value, ast.Call):
                    # `p = functools.partial(f, 1)` / `p = dec()`: a call
                    # whose result is a callable binds like a Callable
                    # annotation.
                    value = self._expr(stmt.value)
                    if value["type"]["kind"] == "func":
                        self.locals[target] = value["type"]
                        return [_assign(target, value, span)]
                    if self._bridge_type(stmt.value) is not None:
                        # Checker bridge: the checker proved the call's result
                        # type; bind the name to it instead of fencing.
                        value = self._expr(stmt.value)
                        if value["type"]["kind"] != "func":
                            self.locals[target] = value["type"]
                            return [_assign(target, value, span)]
                    self.fail(stmt, "PS1001", f"{target} needs an annotation before use")
                if isinstance(stmt.value, ast.Lambda):
                    self.fail(stmt, "PS1001", "a lambda needs a Callable[[args], ret] annotation")
                if self._bridge_type(stmt.value) is not None:
                    # Checker bridge: an unannotated assignment whose inferred
                    # type the subset can express binds without an annotation.
                    value = self._expr(stmt.value)
                    self.locals[target] = value["type"]
                    self._note_frozen(target, stmt.value)
                    return [_assign(target, value, span)]
                self.fail(stmt, "PS1001", f"{target} needs an annotation before use")
            value = self._expr(stmt.value, self.locals[target])
            self._expect(stmt, value["type"], self.locals[target], target)
            # Rebinding a handle frees the dropped value first: the new
            # value overwrites the slot after the free read it (issue #224).
            dropped = self._rebind_handle(target, value["type"], stmt.value, span)
            self._note_frozen(target, stmt.value)
            return [*dropped, _assign(target, value, span)]
        if isinstance(stmt, ast.AugAssign):
            return self._aug_assign(stmt, span)
        if isinstance(stmt, ast.If):
            # `if x is (not) None:` narrows a tagged optional for the branch
            # where the tag is known set: reads of x lower to its `_val`.
            # A name rebound anywhere inside that branch is not narrowed.
            narrow: str | None = None
            narrow_else = False
            test = stmt.test
            if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], (ast.Is, ast.IsNot)):
                left, right = test.left, test.comparators[0]
                probe = left if _is_none_expr(right) else right if _is_none_expr(left) else None
                if isinstance(probe, ast.Name) and probe.id in self.locals:
                    opt_typ = self.locals[probe.id]
                    narrow_else = isinstance(test.ops[0], ast.Is)
                    branch_stmts = stmt.orelse if narrow_else else stmt.body
                    if (
                        opt_typ.get("optional")
                        and probe.id not in self.fn_globals
                        and not any(
                            isinstance(inner, ast.Name)
                            and inner.id == probe.id
                            and isinstance(inner.ctx, (ast.Store, ast.Del))
                            for item in branch_stmts
                            for inner in ast.walk(item)
                        )
                    ):
                        narrow = probe.id
            if narrow is not None:
                self.opt_unwrap[narrow] = self.locals[narrow]
                try:
                    scoped = self._body(list(stmt.orelse if narrow_else else stmt.body))
                finally:
                    self.opt_unwrap.pop(narrow, None)
                if narrow_else:
                    then_body, else_body = self._body(list(stmt.body)), scoped
                else:
                    then_body, else_body = scoped, self._body(list(stmt.orelse))
            else:
                then_body = self._body(list(stmt.body))
                else_body = self._body(list(stmt.orelse))
            return [
                {
                    "kind": "if",
                    "cond": self._bool_cond_of(stmt.test, {"kind": "bool"}),
                    "then": then_body,
                    "else": else_body,
                    "span": span,
                }
            ]
        if isinstance(stmt, ast.Assert):
            return self._assert(stmt, span)
        if isinstance(stmt, ast.While):
            saved_break = self.break_flag
            saved_cont = self.continue_flag
            saved_broke = self.broke_flag
            self.break_flag = self._loop_flag(list(stmt.body))
            self.continue_flag = self._continue_loop_flag(list(stmt.body))
            # The else block runs when no `break` fired. The break flag itself
            # cannot report that: _user_while also sets it on a false cond, so
            # else needs its own flag only a real `break` assigns.
            self.broke_flag = self._temp("els") if stmt.orelse and self.break_flag is not None else None
            if self.broke_flag is not None:
                self.locals[self.broke_flag] = {"kind": "bool"}
            try:
                saved_prelude = self.prelude
                self.prelude = []
                try:
                    cond = self._bool_cond(stmt.test)
                    cond_pre = self.prelude
                finally:
                    self.prelude = saved_prelude
                body = [*self._continue_reset(span), *self._body(list(stmt.body))]
                out = self._user_while(cond, body, span, cond_pre=cond_pre)
            finally:
                broke = self.broke_flag
                self.break_flag = saved_break
                self.continue_flag = saved_cont
                self.broke_flag = saved_broke
            if stmt.orelse:
                # Lowered with the enclosing loop's flags: a `break` inside
                # `else:` belongs to an outer loop, not to this one.
                else_body = self._body(list(stmt.orelse))
                if broke is None:
                    return [*out, *else_body]
                return [
                    _assign(broke, _const_bool(False, span), span),
                    *out,
                    {
                        "kind": "if",
                        "cond": _not_bool(broke, span),
                        "then": else_body,
                        "else": [],
                        "span": span,
                    },
                ]
            return out
        if isinstance(stmt, ast.Break):
            if self.break_flag is None:
                self.fail(stmt, "PS2002", "break outside a loop is not lowered")
            out = [_assign(self.break_flag, _const_bool(True, span), span)]
            if self.broke_flag is not None:
                out.append(_assign(self.broke_flag, _const_bool(True, span), span))
            return out
        if isinstance(stmt, ast.Continue):
            if self.continue_flag is None:
                self.fail(stmt, "PS2002", "continue outside a loop is not lowered")
            return [_assign(self.continue_flag, _const_bool(True, span), span)]
        if isinstance(stmt, ast.For):
            return self._for(stmt, span)
        if isinstance(stmt, ast.Return):
            if self.inst_sig is not None:
                _params, ret = self.inst_sig
            elif self.function in self.sigs:
                _params, ret = self.sigs[self.function or ""]
            else:
                _params, ret = self._current_method()
            if stmt.value is None or (isinstance(stmt.value, ast.Constant) and stmt.value.value is None):
                if ret.get("optional"):
                    return [{"kind": "return", "value": self._opt_box(None, ret, span), "span": span}]
                if ret["kind"] != "none":
                    self.fail(stmt, "PS1003", "bare return is None, and this function does not return None")
                return [{"kind": "return", "value": _const_none(span), "span": span}]
            value = self._expr(stmt.value, ret)
            self._expect(stmt, value["type"], ret, "return")
            # A returned handle transfers ownership to the caller: the scope
            # exit must not free it too (issue #224).
            if value["kind"] == "name" and is_handle_type(value["type"]):
                self.returned_handles.add(value["id"])
            return [{"kind": "return", "value": value, "span": span}]
        if isinstance(stmt, ast.Expr):
            return [{"kind": "expr", "expr": self._expr(stmt.value), "span": span}]
        if isinstance(stmt, ast.Raise):
            return [self._raise(stmt, span)]
        if isinstance(stmt, ast.Try):
            return [self._try(stmt, span)]
        if isinstance(stmt, ast.With):
            return self._with(stmt, span)
        if isinstance(stmt, ast.Match):
            return self._match(stmt, span)
        if isinstance(stmt, ast.Delete):
            return self._delete(stmt, span)
        if isinstance(stmt, ast.Import | ast.ImportFrom):
            # A function-local import binds the module name for the rest of
            # this body only; the binding tables restore when the function
            # finishes lowering. It emits no IR, like the module-level form.
            inner: list[Fence] = []
            if isinstance(stmt, ast.Import):
                self._bind_import(stmt, inner)
            else:
                self._bind_from(stmt, inner)
            if inner:
                first = inner[0]
                raise Fence(first.code, first.message, first.span, self.function)
            return [{"kind": "pass", "span": span}]
        self.fail(stmt, "PS2002", f"{type(stmt).__name__} is not lowered")
        raise AssertionError("unreachable")

    def _current_method(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if self.function and "." in self.function:
            class_name, method = self.function.split(".", 1)
            return self.classes[class_name]["methods"][method]
        self.fail(ast.Pass(), "PS2002", "return is outside a function")
        raise AssertionError("unreachable")

    def _match(self, stmt: ast.Match, span: dict[str, Any]) -> list[dict[str, Any]]:
        """`match` is an if chain; a case's prelude (binds, guard eval) runs only when earlier cases miss."""
        subject_expr = self._expr(stmt.subject)
        temp = self._temp("subj")
        self.locals[temp] = subject_expr["type"]
        self.prelude.append(_assign(temp, subject_expr, span))
        for case in stmt.cases:
            pat = case.pattern
            if not (isinstance(pat, ast.MatchAs) and pat.pattern is None and pat.name) or pat.name in self.locals:
                continue
            self.locals[pat.name] = subject_expr["type"]  # fresh capture: zero-init like a `for` target
            if subject_expr["type"]["kind"] != "none":
                self.prelude.append(_assign(pat.name, _zero(subject_expr["type"], span), span))
        chain: list[dict[str, Any]] = []
        for case in reversed(stmt.cases):
            saved, self.prelude = self.prelude, []
            cond = self._match_case(case, temp, span)
            then = self._body(list(case.body))
            self.prelude, pre = saved, self.prelude
            chain = [*pre, {"kind": "if", "cond": cond, "then": then, "else": chain, "span": span}]
        return chain

    def _match_case(self, case: ast.match_case, subject: str, span: dict[str, Any]) -> dict[str, Any]:
        """A case's condition: alternatives or'd, guard and'd; a capture binds the subject."""
        pat = case.pattern
        values: list[ast.expr] = []
        always = False
        for part in pat.patterns if isinstance(pat, ast.MatchOr) else [pat]:
            if isinstance(part, ast.MatchAs) and part.pattern is None:
                if part.name is not None and isinstance(pat, ast.MatchOr):
                    self.fail(part, "PS2002", "a capture pattern is not lowered")
                if part.name is not None:
                    self._expect(part, self.locals[subject], self.locals[part.name], part.name)
                    self.prelude.append(_assign(part.name, _name(subject, self.locals[subject], span), span))
                always = True
                continue
            if isinstance(part, ast.MatchValue) and isinstance(part.value, ast.Constant):
                right, op = part.value, ast.Eq()
            elif isinstance(part, ast.MatchSingleton) and part.value is None:
                right, op = ast.Constant(value=None), ast.Is()
            else:
                self._refuse_pattern(part)
            left = ast.copy_location(ast.Name(id=subject, ctx=ast.Load()), part)
            compare = ast.copy_location(ast.Compare(left=left, ops=[op], comparators=[right]), part)
            values.append(compare)
        test: ast.expr | None = None
        if values and not always:
            test = values[0] if len(values) == 1 else ast.BoolOp(op=ast.Or(), values=values)
        if case.guard is not None:
            test = case.guard if test is None else ast.BoolOp(op=ast.And(), values=[test, case.guard])
        if test is not None:
            ast.copy_location(test, pat)
        return _const_bool(True, span) if test is None else self._bool_cond(test)

    def _refuse_pattern(self, pat: ast.pattern) -> None:
        if isinstance(pat, ast.MatchAs):
            if pat.name is None:
                self.fail(pat, "PS2002", "a wildcard pattern is not lowered")
            self.fail(pat, "PS2002", "a capture pattern is not lowered")
        if isinstance(pat, ast.MatchOr):
            self.fail(pat, "PS2002", "an or pattern is not lowered")
        if isinstance(pat, ast.MatchSequence) or isinstance(pat, ast.MatchStar):
            self.fail(pat, "PS2002", "a sequence pattern is not lowered")
        if isinstance(pat, ast.MatchClass):
            self.fail(pat, "PS2002", "a class pattern is not lowered")
        if isinstance(pat, ast.MatchMapping):
            self.fail(pat, "PS2002", "a mapping pattern is not lowered")
        if isinstance(pat, ast.MatchSingleton):
            self.fail(pat, "PS2002", "a singleton pattern is not lowered")
        if isinstance(pat, ast.MatchValue):
            self.fail(pat, "PS2002", "a match value that is not a literal is not lowered")
        self.fail(pat, "PS2002", f"{type(pat).__name__} is not lowered")

    def _delete(self, stmt: ast.Delete, span: dict[str, Any]) -> list[dict[str, Any]]:
        """`del` of one unread local is dropped.

        The name stays in the local table so the earlier assignment still
        type-checks, and nothing reads it. A later load stays fenced: this
        compiler cannot raise UnboundLocalError, and an empty `for` target
        already reads as 0. Other targets stay fenced.
        """
        if len(stmt.targets) != 1:
            self.fail(stmt, "PS2002", "del of more than one name is not lowered")
        target = stmt.targets[0]
        if isinstance(target, ast.Attribute):
            self.fail(stmt, "PS2002", "del of an attribute is not lowered")
        if isinstance(target, ast.Subscript):
            if isinstance(target.slice, ast.Slice):
                self.fail(stmt, "PS2002", "del of a slice is not lowered")
            # del d[k] lowers to ps_map_del for a declared dict local; every
            # other subscript target keeps the subscript fence.
            if isinstance(target.value, ast.Name):
                local = self.locals.get(target.value.id, {})
                kind = local.get("kind")
                if kind == "dict":
                    container = self._expr(target.value)
                    index = self._expr(target.slice)
                    self._expect(stmt, index["type"], container["type"]["key"], "dict key")
                    call = self._abi_call(
                        "builtins",
                        "dict.delitem",
                        [container, index],
                        {"kind": "none"},
                        "ps_map_del",
                        span,
                    )
                    return [{"kind": "expr", "expr": call, "span": span}]
                if kind == "list" and not local.get("set"):
                    container = self._expr(target.value)
                    index = self._expr(target.slice)
                    if index["type"]["kind"] != "int":
                        self.fail(stmt, "PS1003", "a list index is int")
                    call = self._abi_call(
                        "builtins",
                        "list.delitem",
                        [container, index],
                        {"kind": "none"},
                        "ps_seq_del",
                        span,
                    )
                    return [{"kind": "expr", "expr": call, "span": span}]
            self.fail(stmt, "PS2002", "del of a subscript is not lowered")
        if isinstance(target, (ast.Tuple, ast.List)):
            self.fail(stmt, "PS2002", "del of a tuple of names is not lowered")
        if not isinstance(target, ast.Name):
            self.fail(stmt, "PS2002", "del of this target is not lowered")
        name = target.id
        if name in self.fn_globals:
            self.fail(stmt, "PS2002", "del of a global is not lowered")
        if name not in self.locals:
            self.fail(stmt, "PS2002", "del of an unbound name is not lowered")
        if self._name_read_after(stmt, name):
            self.fail(stmt, "PS2002", "del of a name that is read later is not lowered")
        return [{"kind": "pass", "span": span}]

    def _name_read_after(self, delete: ast.Delete, name: str) -> bool:
        state = {"seen": False, "hit": False}

        def visit(node: ast.AST) -> None:
            if state["hit"]:
                return
            if node is delete:
                state["seen"] = True
                return
            if state["seen"] and isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == name:
                state["hit"] = True
                return
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return
            for child in ast.iter_child_nodes(node):
                visit(child)

        for stmt in self.fn_body:
            visit(stmt)
        return (not state["seen"]) or state["hit"]

    def _aug_assign(self, stmt: ast.AugAssign, span: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(stmt.target, ast.Name):
            self.fail(stmt, "PS2002", "augmented assignment is only lowered for a name")
        target = stmt.target.id
        if (
            target in self.fn_globals
            or target in self.fn_nonlocals
            or (self.function == self.entry_fn_name and target in self.global_slots)
        ):
            assign = ast.Assign(
                targets=[stmt.target],
                value=ast.BinOp(left=ast.Name(id=target, ctx=ast.Load()), op=stmt.op, right=stmt.value),
            )
            ast.copy_location(assign, stmt)
            ast.copy_location(assign.value, stmt)
            ast.copy_location(assign.value.left, stmt)
            return self._stmt_core(assign)
        if target not in self.locals:
            self.fail(stmt, "PS1001", f"{target} needs an annotation before use")
        left = ast.Name(id=target, ctx=ast.Load())
        ast.copy_location(left, stmt)
        node = ast.BinOp(left=left, op=stmt.op, right=stmt.value)
        ast.copy_location(node, stmt)
        value = self._expr(node)
        self._expect(stmt, value["type"], self.locals[target], target)
        return [_assign(target, value, span)]

    def _spread_assign(self, stmt: ast.Assign, span: dict[str, Any]) -> list[dict[str, Any]]:
        """Chained (`a = b = c`) and destructuring (`a, b = pair`, `first, *rest = xs`) targets.

        The RHS lowers once; a non-name value pins to a temp when several
        targets share it or a destructuring needs repeated reads.
        """
        value = self._expr(stmt.value)
        stmts: list[dict[str, Any]] = []
        for target in stmt.targets:
            if isinstance(target, (ast.Tuple, ast.List)):
                value = self._pin(value, stmts, span)
                stmts.extend(self._unpack(stmt, target.elts, value, span))
            elif isinstance(target, ast.Name):
                if len(stmt.targets) > 1:
                    value = self._pin(value, stmts, span)
                stmts.append(self._store_target(stmt, target.id, value, span))
                self._note_frozen(target.id, stmt.value)
            else:
                self.fail(stmt, "PS2002", "only a single name can be assigned")
        return stmts

    def _pin(self, value: dict[str, Any], stmts: list[dict[str, Any]], span: dict[str, Any]) -> dict[str, Any]:
        if value["kind"] == "name":
            return value
        holder = self._temp("hold")
        self.locals[holder] = value["type"]
        stmts.append(_assign(holder, value, span))
        return _name(holder, value["type"], span)

    def _store_target(self, node: ast.AST, name: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        """One name target: a global slot stores through `ps_global_set`; a local binds."""
        if name in self.fn_globals:
            slot = self.global_slots.get(name)
            if slot is None or not slot.get("active"):
                self.fail(node, "PS2002", f"global {name} is not a module slot")
            self._expect(node, value["type"], slot["type"], name)
            return self._global_set(name, value, span)
        self._bind_target(name, value["type"], node)
        return _assign(name, value, span)

    def _bind_target(self, name: str, typ: dict[str, Any], node: ast.AST) -> None:
        """An unpack or chained target binds a fresh name implicitly, like a for target."""
        if name in self.bindings or name in self.classes:
            self.fail(node, "PS2002", f"{name} is already bound and cannot be an unpack target")
        if name not in self.locals:
            self.locals[name] = typ
            return
        if not types_equal(self.locals[name], typ):
            self.fail(node, "PS1003", f"{name} is {_show(self.locals[name])}, and the unpack target is {_show(typ)}")

    def _unpack(
        self, node: ast.AST, elts: list[ast.expr], value: dict[str, Any], span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """A tuple or list destructuring target.

        Element targets are names or a nested tuple/list; a single `*name`
        collects the middle. The value is a tuple (static field reads) or a
        list (runtime length checks).
        """
        star = -1
        for index, elt in enumerate(elts):
            inner = elt.value if isinstance(elt, ast.Starred) else elt
            if not isinstance(inner, (ast.Name, ast.Tuple, ast.List)):
                self.fail(elt, "PS2002", "unpack targets are names")
            if isinstance(elt, ast.Starred):
                if star >= 0:
                    self.fail(elt, "PS2002", "unpack keeps a single star")
                star = index
        typ = value["type"]
        if typ["kind"] == "class" and self.classes.get(typ["name"], {}).get("tuple"):
            return self._unpack_fixed(node, elts, star, value, span)
        if typ["kind"] == "list" and not typ.get("set"):
            return self._unpack_sized(elts, star, value, span)
        self.fail(node, "PS2002", "unpacking needs a tuple or list value")
        raise AssertionError("unreachable")

    def _unpack_fixed(
        self, node: ast.AST, elts: list[ast.expr], star: int, value: dict[str, Any], span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """A tuple RHS reads fields; a wrong name count is a static fence (the checker rejects it first anyway)."""
        fields = self.classes[value["type"]["name"]]["fields"]
        if star < 0:
            if len(fields) != len(elts):
                self.fail(node, "PS2002", f"unpacking a {len(fields)}-tuple needs {len(fields)} names")
        elif len(fields) < len(elts) - 1:
            self.fail(node, "PS2002", f"starred unpack of a {len(fields)}-tuple needs {len(fields)} fields")
        tail = len(elts) - star - 1 if star >= 0 else 0
        span_fields = fields[star : len(fields) - tail] if star >= 0 else []
        elem: dict[str, Any] | None = None
        if star >= 0:
            if not span_fields:
                self.fail(elts[star], "PS2002", "starred unpack needs an element type")
            elem = span_fields[0]["type"]
            if any(not types_equal(field["type"], elem) for field in span_fields):
                self.fail(elts[star], "PS2002", "starred unpack needs uniform element types")
        stmts: list[dict[str, Any]] = []
        for index, elt in enumerate(elts):
            if index == star:
                member: dict[str, Any] = {
                    "kind": "list",
                    "elems": [_attr(value, field["name"], field["type"], span) for field in span_fields],
                    "type": {"kind": "list", "elem": elem},
                    "span": span,
                }
            else:
                f_index = index if star < 0 or index < star else len(fields) - (len(elts) - index)
                member = _attr(value, fields[f_index]["name"], fields[f_index]["type"], span)
            stmts.extend(self._unpack_name(elt, member, span))
        return stmts

    def _unpack_sized(
        self, elts: list[ast.expr], star: int, value: dict[str, Any], span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """A list RHS length-checks at run time; a wrong count raises the CPython ValueError."""
        stmts: list[dict[str, Any]] = []
        if value["kind"] != "name":
            seq = self._temp("seq")
            self.locals[seq] = value["type"]
            stmts.append(_assign(seq, value, span))
            value = _name(seq, value["type"], span)
        length = _len_call(value, span)
        got: dict[str, Any] = {
            "kind": "call",
            "callee": {"kind": "builtin", "name": "str"},
            "args": [length],
            "type": {"kind": "str"},
            "span": span,
        }
        need = len(elts) - 1 if star >= 0 else len(elts)
        checks: list[tuple[dict[str, Any], dict[str, Any]]] = []
        if star < 0:
            checks.append(
                (
                    _compare("<", length, _const_int(need, span), span),
                    _concat_str(
                        [
                            _const_str(f"not enough values to unpack (expected {need}, got ", span),
                            got,
                            _const_str(")", span),
                        ],
                        span,
                    ),
                )
            )
            checks.append(
                (
                    _compare(">", length, _const_int(need, span), span),
                    _const_str(f"too many values to unpack (expected {need})", span),
                )
            )
        else:
            checks.append(
                (
                    _compare("<", length, _const_int(need, span), span),
                    _concat_str(
                        [
                            _const_str(f"not enough values to unpack (expected at least {need}, got ", span),
                            got,
                            _const_str(")", span),
                        ],
                        span,
                    ),
                )
            )
        for cond, message in checks:
            stmts.append({"kind": "if", "cond": cond, "then": [_raise_value(message, span)], "else": [], "span": span})
        elem = value["type"]["elem"]
        for index, elt in enumerate(elts):
            if index == star:
                tail = len(elts) - star - 1
                member = self._abi_call(
                    "builtins",
                    "slice",
                    [
                        value,
                        _const_int(star, span),
                        _const_int(-tail, span),
                        _const_int(1, span),
                        _const_int(1 if tail else 0, span),
                        _const_int(1, span),
                    ],
                    {"kind": "list", "elem": elem},
                    "ps_seq_slice",
                    span,
                )
            else:
                offset = index if star < 0 or index < star else index - len(elts)
                member = _item(value, _const_int(offset, span), elem, span)
            stmts.extend(self._unpack_name(elt, member, span))
        return stmts

    def _unpack_name(self, elt: ast.expr, member: dict[str, Any], span: dict[str, Any]) -> list[dict[str, Any]]:
        """One unpack element: a Name binds; a nested tuple/list recurses."""
        if isinstance(elt, ast.Starred):
            elt = elt.value
        if isinstance(elt, ast.Name):
            return [self._store_target(elt, elt.id, member, span)]
        assert isinstance(elt, (ast.Tuple, ast.List))
        return self._unpack(elt, elt.elts, member, span)

    def _for(self, stmt: ast.For, span: dict[str, Any]) -> list[dict[str, Any]]:
        if isinstance(stmt.target, (ast.Tuple, ast.List)):
            for elt in stmt.target.elts:
                if not isinstance(elt, ast.Name):
                    self.fail(elt, "PS2002", "for unpacks flat names")
        elif not isinstance(stmt.target, ast.Name):
            self.fail(stmt, "PS2002", "for targets a single name")
        saved_break = self.break_flag
        saved_cont = self.continue_flag
        saved_broke = self.broke_flag
        self.break_flag = self._loop_flag(list(stmt.body))
        self.continue_flag = self._continue_loop_flag(list(stmt.body))
        self.broke_flag = self._temp("els") if stmt.orelse and self.break_flag is not None else None
        if self.broke_flag is not None:
            self.locals[self.broke_flag] = {"kind": "bool"}
        try:
            out = self._for_dispatch(stmt, span)
        finally:
            broke = self.broke_flag
            self.break_flag = saved_break
            self.continue_flag = saved_cont
            self.broke_flag = saved_broke
        if stmt.orelse:
            else_body = self._body(list(stmt.orelse))
            if broke is None:
                return [*out, *else_body]
            return [
                _assign(broke, _const_bool(False, span), span),
                *out,
                {
                    "kind": "if",
                    "cond": _not_bool(broke, span),
                    "then": else_body,
                    "else": [],
                    "span": span,
                },
            ]
        return out

    def _for_dispatch(self, stmt: ast.For, span: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(stmt.target, ast.Name) and (
            self._is_mod(stmt.iter, "itertools", "repeat")
            or self._is_mod(stmt.iter, "itertools", "chain")
            or _is_range_call(stmt.iter)
        ):
            self.fail(stmt, "PS2002", "for unpacks a list of tuples or enumerate/zip")
        if self._is_mod(stmt.iter, "itertools", "repeat"):
            return self._for_repeat(stmt, span)
        if self._is_mod(stmt.iter, "itertools", "chain"):
            return self._for_chain(stmt, span)
        if _is_range_call(stmt.iter):
            return self._for_range(stmt, span)
        if _is_call_name(stmt.iter, "enumerate"):
            return self._for_enumerate(stmt, span)
        if _is_call_name(stmt.iter, "zip"):
            return self._for_zip(stmt, span)
        iter_node: ast.AST = stmt.iter
        if isinstance(iter_node, ast.Tuple):
            if not iter_node.elts:
                return []
            # A tuple literal iterates its elements in order; the homogeneous
            # case is the list literal this loop already lowers.
            list_iter = ast.List(elts=iter_node.elts, ctx=ast.Load())
            ast.copy_location(list_iter, iter_node)
            iter_node = list_iter
        iterable = self._expr(iter_node)
        kind = iterable["type"]["kind"]
        if kind == "class" and "__iter__" in self.classes.get(iterable["type"]["name"], {}).get("methods", {}):
            called = self._dunder_call(stmt.iter, iterable, "__iter__", [], span)
            if called is not None:
                iterable = called
                kind = iterable["type"]["kind"]
        if kind == "dict":
            key_t = iterable["type"]["key"]
            if key_t["kind"] not in {"int", "str"}:
                self.fail(stmt.iter, "PS2002", "dict keys are int or str")
            # `for k in d` visits keys in insertion order; ps_dict_keys builds
            # the list in that same order.
            iterable = self._abi_call(
                "builtins", "dict.keys", [iterable], {"kind": "list", "elem": key_t}, "ps_dict_keys", span
            )
            kind = "list"
        if kind == "class" and iterable["type"].get("name") in {"TextIO", "BytesIO"}:
            # `for line in f` reads a line at a time until the empty result;
            # each line keeps its trailing newline like CPython file iteration.
            elem = {"kind": "bytes"} if iterable["type"]["name"] == "BytesIO" else {"kind": "str"}
            target = stmt.target.id
            fresh = self._bind_loop_target(target, elem, stmt.target)
            fio = self._temp("fio")
            line = self._temp("line")
            self.locals[fio] = iterable["type"]
            self.locals[line] = elem
            body = self._loop_body(stmt)
            readline = self._abi_call(
                "io", "readline", [_name(fio, iterable["type"], span)], elem, "ps_io_readline", span
            )
            out = [
                _assign(fio, iterable, span),
                _assign(line, readline, span),
                *self._user_while(
                    _compare("!=", _len_call(_name(line, elem, span), span), _const_int(0, span), span),
                    [
                        _assign(target, _name(line, elem, span), span),
                        *body,
                        *self._skip_on_break([_assign(line, readline, span)], span),
                    ],
                    span,
                ),
            ]
            if fresh:
                out.insert(0, _assign(target, _zero(elem, span), span))
            return out
        if kind == "class" and self.classes.get(iterable["type"]["name"], {}).get("iterator"):
            return self._for_iter(stmt, iterable, span)
        if kind == "class" and "__next__" in self.classes.get(iterable["type"]["name"], {}).get("methods", {}):
            return self._for_dunder(stmt, iterable, span)
        if kind == "str":
            elem = {"kind": "str"}
        elif kind == "bytes":
            elem = {"kind": "int"}
        elif kind == "list":
            elem = iterable["type"]["elem"]
            if elem["kind"] not in _SCALAR | {"list", "dict"} and not (
                elem["kind"] == "class" and self.classes.get(elem["name"], {}).get("tuple")
            ):
                self.fail(stmt, "PS2002", f"for over list[{_show(elem)}] is not lowered")
        else:
            self.fail(stmt.iter, "PS2002", "for iterates over a list or range")
        unpack = not isinstance(stmt.target, ast.Name)
        target = ""
        fresh = False
        if unpack:
            if elem["kind"] != "class" or not self.classes.get(elem["name"], {}).get("tuple"):
                self.fail(stmt, "PS2002", "for unpacks a list of tuples or enumerate/zip")
        else:
            target = stmt.target.id
            fresh = self._bind_loop_target(target, elem, stmt.target)
        seq = self._temp("seq")
        idx = self._temp("idx")
        length = self._temp("len")
        self.locals[seq] = iterable["type"]
        self.locals[idx] = {"kind": "int"}
        self.locals[length] = {"kind": "int"}
        int_t = {"kind": "int"}
        item = _item(_name(seq, iterable["type"], span), _name(idx, int_t, span), elem, span)
        if unpack:
            item_t = self._temp("item")
            self.locals[item_t] = elem
            fields = self.classes[elem["name"]]["fields"]
            values = [_attr(_name(item_t, elem, span), field["name"], field["type"], span) for field in fields]
            bind = [
                _assign(item_t, item, span),
                *self._for_unpack(stmt, [field["type"] for field in fields], values, span),
            ]
        else:
            bind = [_assign(target, item, span)]
        body = self._loop_body(stmt)
        step = [
            *bind,
            *body,
            *self._skip_on_break(
                [_assign(idx, _binary_int("+", _name(idx, int_t, span), _const_int(1, span), span), span)],
                span,
            ),
        ]
        prefix = []
        if fresh:
            prefix.append(_assign(target, _zero(elem, span), span))
        prefix.extend(
            [
                _assign(seq, iterable, span),
                _assign(
                    length,
                    {
                        "kind": "call",
                        "callee": {"kind": "builtin", "name": "len"},
                        "args": [_name(seq, iterable["type"], span)],
                        "type": int_t,
                        "span": span,
                    },
                    span,
                ),
                _assign(idx, _const_int(0, span), span),
                *self._user_while(
                    _compare("<", _name(idx, int_t, span), _name(length, int_t, span), span),
                    step,
                    span,
                ),
            ]
        )
        return prefix

    def _for_iter(self, stmt: ast.For, iterable: dict[str, Any], span: dict[str, Any]) -> list[dict[str, Any]]:
        """`for x in it` drains the iterator record: each step reads seq[idx]
        and advances idx, so a second loop over `it` sees the spent cursor."""
        elem = iterable["type"]["elem"]
        int_t = {"kind": "int"}
        seq_t = {"kind": "list", "elem": elem}
        itn = self._temp("it")
        self.locals[itn] = iterable["type"]
        itn_n = _name(itn, iterable["type"], span)
        seq_r = _attr(itn_n, "seq", seq_t, span)
        idx_r = _attr(itn_n, "idx", int_t, span)
        unpack = not isinstance(stmt.target, ast.Name)
        target = ""
        fresh = False
        if unpack:
            if elem["kind"] != "class" or not self.classes.get(elem["name"], {}).get("tuple"):
                self.fail(stmt, "PS2002", "for unpacks a list of tuples or enumerate/zip")
        else:
            target = stmt.target.id
            fresh = self._bind_loop_target(target, elem, stmt.target)
        item = _item(seq_r, idx_r, elem, span)
        if unpack:
            item_t = self._temp("item")
            self.locals[item_t] = elem
            fields = self.classes[elem["name"]]["fields"]
            values = [_attr(_name(item_t, elem, span), field["name"], field["type"], span) for field in fields]
            bind = [
                _assign(item_t, item, span),
                *self._for_unpack(stmt, [field["type"] for field in fields], values, span),
            ]
        else:
            bind = [_assign(target, item, span)]
        advance = {
            "kind": "setattr",
            "object": itn_n,
            "name": "idx",
            "value": _binary_int("+", idx_r, _const_int(1, span), span),
            "span": span,
        }
        body = self._loop_body(stmt)
        out = [_assign(itn, iterable, span)]
        if fresh:
            out.append(_assign(target, _zero(elem, span), span))
        out += self._user_while(
            _compare("<", idx_r, _len_call(seq_r, span), span),
            [*bind, advance, *body],
            span,
        )
        return out

    def _for_dunder(self, stmt: ast.For, iterable: dict[str, Any], span: dict[str, Any]) -> list[dict[str, Any]]:
        """`for x in it` on a class with __next__: call it each step and let a
        raised StopIteration end the loop — CPython's iterator drain."""
        it_t = iterable["type"]
        elem = self.classes[it_t["name"]]["methods"]["__next__"][1]
        if not isinstance(stmt.target, ast.Name):
            self.fail(stmt, "PS2002", "for unpacks a list of tuples or enumerate/zip")
        target = stmt.target.id
        fresh = self._bind_loop_target(target, elem, stmt.target)
        itn = self._temp("it")
        self.locals[itn] = it_t
        flag = self._temp("itr")
        self.locals[flag] = {"kind": "bool"}
        step = self._dunder_call(stmt, _name(itn, it_t, span), "__next__", [], span)
        if step is None:
            self.fail(stmt, "PS2001", f"{it_t['name']}.__next__ is not a method")
        saved_break = self.break_flag
        self.break_flag = flag
        body = self._loop_body(stmt)
        guarded = self._skip_on_break(body, span)
        self.break_flag = saved_break
        out = [_assign(itn, iterable, span), _assign(flag, _const_bool(False, span), span)]
        if fresh:
            out.append(_assign(target, _zero(elem, span), span))
        out.append(
            {
                "kind": "while",
                "cond": _not_bool(flag, span),
                "body": [
                    {
                        "kind": "try",
                        "body": [_assign(target, step, span)],
                        "handlers": [
                            {
                                "exc": ["StopIteration"],
                                "codes": [8],
                                "body": [_assign(flag, _const_bool(True, span), span)],
                            }
                        ],
                        "span": span,
                    },
                    *guarded,
                ],
                "span": span,
            }
        )
        return out

    def _for_range(self, stmt: ast.For, span: dict[str, Any]) -> list[dict[str, Any]]:
        call = stmt.iter
        assert isinstance(call, ast.Call)
        if call.keywords:
            self.fail(call, "PS2003", "keyword arguments are not lowered")
        args = [self._expr(arg, {"kind": "int"}) for arg in call.args]
        for arg in args:
            if arg["type"]["kind"] != "int":
                self.fail(call, "PS1003", "range arguments are int")
        if len(args) == 1:
            start, stop, step = _const_int(0, span), args[0], _const_int(1, span)
        elif len(args) == 2:
            start, stop, step = args[0], args[1], _const_int(1, span)
        elif len(args) == 3:
            start, stop, step = args
        else:
            self.fail(call, "PS2003", "range takes 1, 2, or 3 arguments")
        target = stmt.target.id
        assert isinstance(stmt.target, ast.Name)
        fresh = self._bind_loop_target(target, {"kind": "int"}, stmt.target)
        cur = self._temp("cur")
        stop_n = self._temp("stop")
        step_n = self._temp("step")
        int_t = {"kind": "int"}
        self.locals[cur] = int_t
        self.locals[stop_n] = int_t
        self.locals[step_n] = int_t
        body = self._loop_body(stmt)
        advance = self._skip_on_break(
            [_assign(cur, _binary_int("+", _name(cur, int_t, span), _name(step_n, int_t, span), span), span)],
            span,
        )
        up = [_assign(target, _name(cur, int_t, span), span), *body, *advance]
        down = [_assign(target, _name(cur, int_t, span), span), *body, *advance]
        prefix = []
        if fresh:
            prefix.append(_assign(target, _const_int(0, span), span))
        prefix.extend(
            [
                *self._break_init(span),
                _assign(cur, start, span),
                _assign(stop_n, stop, span),
                _assign(step_n, step, span),
                {
                    "kind": "if",
                    "cond": _compare("==", _name(step_n, int_t, span), _const_int(0, span), span),
                    "then": [{"kind": "raise", "exc": "ValueError", "message": None, "span": span}],
                    "else": [],
                    "span": span,
                },
                {
                    "kind": "if",
                    "cond": _compare(">", _name(step_n, int_t, span), _const_int(0, span), span),
                    "then": self._user_while(
                        _compare("<", _name(cur, int_t, span), _name(stop_n, int_t, span), span),
                        up,
                        span,
                        init=False,
                    ),
                    "else": self._user_while(
                        _compare(">", _name(cur, int_t, span), _name(stop_n, int_t, span), span),
                        down,
                        span,
                        init=False,
                    ),
                    "span": span,
                },
            ]
        )
        return prefix

    def _bind_loop_target(self, name: str, typ: dict[str, Any], node: ast.AST) -> bool:
        if name in self.bindings or name in self.classes:
            self.fail(node, "PS2002", f"{name} is already bound and cannot be a for target")
        if name not in self.locals:
            self.locals[name] = typ
            return True
        if not types_equal(self.locals[name], typ):
            self.fail(node, "PS1003", f"{name} is {_show(self.locals[name])}, and the for target is {_show(typ)}")
        return False

    def _assert(self, stmt: ast.Assert, span: dict[str, Any]) -> list[dict[str, Any]]:
        """`assert cond` is `if not cond: raise AssertionError`, and a message sits on that raise.

        The condition is whatever `if` already accepts. AssertionError is in
        the exception table (#515), so `except AssertionError` catches the
        failure the way CPython does. The message is a `str`, same as
        `raise`, and it is lowered only in the failing branch.
        """
        cond = self._bool_cond(stmt.test)
        then: list[dict[str, Any]] = []
        message: dict[str, Any] | None = None
        if stmt.msg is not None:
            saved = self.prelude
            self.prelude = []
            try:
                message = self._expr(stmt.msg, {"kind": "str"})
                if message["type"]["kind"] != "str":
                    self.fail(stmt.msg, "PS1003", "an exception message is a str")
                then = self.prelude
            finally:
                self.prelude = saved
        then.append({"kind": "raise", "exc": "AssertionError", "message": message, "span": span})
        negated = {"kind": "unary", "op": "not", "operand": cond, "type": {"kind": "bool"}, "span": span}
        return [{"kind": "if", "cond": negated, "then": then, "else": [], "span": span}]

    def _raise(self, stmt: ast.Raise, span: dict[str, Any]) -> dict[str, Any]:
        # `E(arg)` becomes the message (str(arg)); bool goes through str() so
        # True prints "True". The exc and `raise ... from` calls share one
        # pass; multi-arg ctors fence (repr(tuple) is not cheap).
        msgs: list[dict[str, Any] | None] = [None, None]
        for which in range(2):
            call = stmt.exc if which == 0 else stmt.cause
            if not isinstance(call, ast.Call):
                continue
            if call.keywords:
                self.fail(call, "PS2003", "keyword arguments are not lowered")
            built: dict[str, Any] | None = None
            if call.args:
                if len(call.args) != 1:
                    self.fail(call, "PS2003", "exception takes one scalar argument")
                expr = self._expr(call.args[0])
                if expr["type"]["kind"] == "bool":
                    built = {
                        "kind": "call",
                        "callee": {"kind": "builtin", "name": "str"},
                        "args": [expr],
                        "type": {"kind": "str"},
                        "span": span,
                    }
                elif expr["type"]["kind"] in {"str", "int", "float"}:
                    built = self._as_str(expr, call)
                else:
                    self.fail(call, "PS2003", "exception argument is a scalar")
            msgs[which] = built
        message, cmessage = msgs
        if stmt.exc is None:
            if not self.exc_stack:
                self.fail(stmt, "PS2002", "bare raise is only lowered inside an except handler")
            slot = self.exc_stack[-1]
            if not slot:
                slot = self._temp("exc")
                self.locals[slot] = {"kind": "exc"}
                self.exc_stack[-1] = slot
            node: dict[str, Any] = {"kind": "raise", "exc": "Exception", "reraise": True, "excslot": slot, "span": span}
        else:
            exc: ast.expr = stmt.exc
            if isinstance(exc, ast.Call):
                exc = exc.func
            if not isinstance(exc, ast.Name):
                self.fail(stmt, "PS2002", "raise names a lowered exception")
            if exc.id in EXCEPTIONS:
                node = {"kind": "raise", "exc": exc.id, "message": message, "span": span}
            elif exc.id in RESERVED_EXCEPTIONS:
                # A reserved class (queue.Empty): the code rides the node.
                node = {
                    "kind": "raise",
                    "exc": exc.id,
                    "code": RESERVED_EXCEPTIONS[exc.id],
                    "message": message,
                    "span": span,
                }
            else:
                resolved = self.class_aliases.get(exc.id, exc.id)
                info = self.classes.get(resolved)
                if info is None or not info.get("exception"):
                    self.fail(stmt, "PS2002", "raise names a lowered exception")
                # raise makes a code plus optional str; it does not call __init__.
                node = {"kind": "raise", "exc": resolved, "code": info["exc_code"], "message": message, "span": span}
        if stmt.cause is not None:
            cause_node = stmt.cause
            if isinstance(cause_node, ast.Name) and self.locals.get(cause_node.id, {}).get("kind") == "exc":
                node["cause"] = {"kind": "name", "id": cause_node.id, "type": {"kind": "exc"}, "span": span}
            elif not (isinstance(cause_node, ast.Constant) and cause_node.value is None):
                cexc = cause_node
                if isinstance(cause_node, ast.Call):
                    cexc = cause_node.func
                if not isinstance(cexc, ast.Name):
                    self.fail(cause_node, "PS2002", "raise-from needs an exception or None")
                if cexc.id in EXCEPTIONS:
                    node["cause"] = {"kind": "excmake", "exc": cexc.id, "message": cmessage, "span": span}
                else:
                    cresolved = self.class_aliases.get(cexc.id, cexc.id)
                    cinfo = self.classes.get(cresolved)
                    if cinfo is None or not cinfo.get("exception"):
                        self.fail(cause_node, "PS2002", "raise-from needs an exception or None")
                    node["cause"] = {
                        "kind": "excmake",
                        "exc": cresolved,
                        "code": cinfo["exc_code"],
                        "message": cmessage,
                        "span": span,
                    }
        return node

    def _try(self, stmt: ast.Try, span: dict[str, Any]) -> dict[str, Any]:
        # brk/cont flags tell the else path whether the try body jumped out.
        brk = self.break_flag
        cont = self.continue_flag
        handlers = []
        for handler in stmt.handlers:
            names: list[str] = []
            codes: list[int] = []
            if handler.type is not None:
                names = _handler_names(self, handler.type)
                if "Exception" not in names:
                    for name in names:
                        codes.extend(self._exception_codes(name))
            bind = handler.name
            if bind is not None:
                prior = self.locals.get(bind)
                if prior is None:
                    self.locals[bind] = {"kind": "exc"}
                elif prior.get("kind") != "exc":
                    self.fail(handler, "PS1205", f"{bind} is {_show(prior)}, and except-as binds an exception")
                self.dead_names.discard(bind)
            self.exc_stack.append(bind or "")
            try:
                body = self._body(list(handler.body))
            finally:
                slot = self.exc_stack.pop()
                if bind is not None:
                    self.dead_names.add(bind)  # CPython emits `del e` at handler end

            node: dict[str, Any] = {"exc": names, "codes": codes, "body": body}
            if handler.type is None:
                node["bare"] = True
            if slot:
                node["excslot"] = slot
            elif bind is not None:
                node["excslot"] = bind
            if bind is not None:
                node["bind"] = bind
            handlers.append(node)
        node = {"kind": "try", "body": self._body(list(stmt.body)), "handlers": handlers, "span": span}
        if stmt.orelse:
            node["orelse"] = self._body(list(stmt.orelse))
            if brk is not None:
                node["brk"] = brk
            if cont is not None:
                node["cont"] = cont
        if stmt.finalbody:
            node["finalbody"] = self._body(list(stmt.finalbody))
        return node

    def _with(self, stmt: ast.With, span: dict[str, Any]) -> list[dict[str, Any]]:
        if len(stmt.items) > 1:
            # `with a, b:` is `with a: with b:` — nest the tail so each
            # item lowers through the single-item dispatch below.
            inner = ast.copy_location(ast.With(items=list(stmt.items[1:]), body=list(stmt.body)), stmt)
            stmt = ast.copy_location(ast.With(items=[stmt.items[0]], body=[inner]), stmt)
        item = stmt.items[0]
        ctx = item.context_expr
        if isinstance(ctx, ast.Call) and isinstance(ctx.func, ast.Name) and ctx.func.id == "open":
            return self._with_open(stmt, item, span)
        if self._named_temp_call(ctx):
            return self._with_open(stmt, item, span)
        if self._is_mod(ctx, "importlib.resources", "as_file"):
            return self._with_as_file(stmt, item, span)
        if self._is_mod(ctx, "contextlib", "suppress"):
            if item.optional_vars is not None:
                self.fail(stmt, "PS1206", "with-as is not lowered")
            assert isinstance(ctx, ast.Call)
            if len(ctx.args) != 1 or not isinstance(ctx.args[0], ast.Name) or ctx.args[0].id not in EXCEPTIONS:
                self.fail(ctx, "PS2002", "suppress takes one lowered exception")
            return [
                {
                    "kind": "try",
                    "body": self._body(list(stmt.body)),
                    "handlers": [{"exc": [ctx.args[0].id], "body": []}],
                    "span": span,
                }
            ]
        if self._is_mod(ctx, "contextlib", "nullcontext"):
            if item.optional_vars is not None:
                self.fail(stmt, "PS1206", "with-as is not lowered")
            assert isinstance(ctx, ast.Call)
            if ctx.args:
                self.fail(ctx, "PS2003", "nullcontext takes no arguments")
            return self._body(list(stmt.body))
        ctx_v = self._expr(ctx)
        if ctx_v["type"]["kind"] == "class" and ctx_v["type"].get("name") in {"TextIO", "BytesIO"}:
            return self._with_io(stmt, item, ctx_v, span)
        if ctx_v["type"]["kind"] == "class" and "__enter__" in self.classes.get(ctx_v["type"].get("name"), {}).get(
            "methods", {}
        ):
            return self._with_cm(stmt, item, ctx_v, span)
        if item.optional_vars is not None:
            self.fail(stmt, "PS1206", "with-as is not lowered")
        self.fail(
            stmt,
            "PS2002",
            "with is lowered for contextlib.suppress, contextlib.nullcontext, open, and importlib.resources.as_file",
        )
        raise AssertionError("unreachable")

    def _for_repeat(self, stmt: ast.For, span: dict[str, Any]) -> list[dict[str, Any]]:
        call = stmt.iter
        assert isinstance(call, ast.Call)
        if len(call.args) != 2:
            self.fail(call, "PS2003", "itertools.repeat takes a value and a count")
        value = self._expr(call.args[0])
        count = self._expr(call.args[1])
        if count["type"]["kind"] != "int":
            self.fail(call, "PS1003", "itertools.repeat count is int")
        if value["type"]["kind"] not in _SCALAR:
            self.fail(call, "PS2002", "itertools.repeat takes a scalar")
        target = stmt.target.id
        assert isinstance(stmt.target, ast.Name)
        fresh = self._bind_loop_target(target, value["type"], stmt.target)
        held = self._temp("rep")
        idx = self._temp("idx")
        stop = self._temp("stop")
        int_t = {"kind": "int"}
        self.locals[held] = value["type"]
        self.locals[idx] = int_t
        self.locals[stop] = int_t
        body = self._loop_body(stmt)
        prefix: list[dict[str, Any]] = []
        if fresh:
            prefix.append(_assign(target, _zero(value["type"], span), span))
        prefix.extend(
            [
                _assign(held, value, span),
                _assign(idx, _const_int(0, span), span),
                _assign(stop, count, span),
                *self._user_while(
                    _compare("<", _name(idx, int_t, span), _name(stop, int_t, span), span),
                    [
                        _assign(target, _name(held, value["type"], span), span),
                        *body,
                        *self._skip_on_break(
                            [_assign(idx, _binary_int("+", _name(idx, int_t, span), _const_int(1, span), span), span)],
                            span,
                        ),
                    ],
                    span,
                ),
            ]
        )
        return prefix

    def _for_chain(self, stmt: ast.For, span: dict[str, Any]) -> list[dict[str, Any]]:
        call = stmt.iter
        assert isinstance(call, ast.Call)
        if not 2 <= len(call.args) <= 8:
            self.fail(call, "PS2003", "itertools.chain takes 2 to 8 lists")
        lists = [self._expr(arg) for arg in call.args]
        if any(item["type"]["kind"] != "list" for item in lists):
            self.fail(call, "PS2002", "itertools.chain takes lists")
        elem = lists[0]["type"]["elem"]
        if elem["kind"] not in _SCALAR:
            self.fail(call, "PS2002", f"for over list[{_show(elem)}] is not lowered")
        for item in lists[1:]:
            if not types_equal(item["type"]["elem"], elem):
                self.fail(call, "PS1003", "itertools.chain lists have one element type")
        target = stmt.target.id
        assert isinstance(stmt.target, ast.Name)
        fresh = self._bind_loop_target(target, elem, stmt.target)
        body = self._loop_body(stmt)
        prefix: list[dict[str, Any]] = []
        if fresh:
            prefix.append(_assign(target, _zero(elem, span), span))
        prefix.extend(self._break_init(span))
        for item in lists:
            prefix.extend(self._list_loop(target, item, body, span))
        return prefix

    def _list_loop(
        self, target: str, iterable: dict[str, Any], body: list[dict[str, Any]], span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        elem = iterable["type"]["elem"]
        seq = self._temp("seq")
        idx = self._temp("idx")
        length = self._temp("len")
        int_t = {"kind": "int"}
        self.locals[seq] = iterable["type"]
        self.locals[idx] = int_t
        self.locals[length] = int_t
        return [
            _assign(seq, iterable, span),
            _assign(
                length,
                {
                    "kind": "call",
                    "callee": {"kind": "builtin", "name": "len"},
                    "args": [_name(seq, iterable["type"], span)],
                    "type": int_t,
                    "span": span,
                },
                span,
            ),
            _assign(idx, _const_int(0, span), span),
            *self._user_while(
                _compare("<", _name(idx, int_t, span), _name(length, int_t, span), span),
                [
                    _assign(
                        target,
                        {
                            "kind": "subscript",
                            "value": _name(seq, iterable["type"], span),
                            "index": _name(idx, int_t, span),
                            "type": elem,
                            "span": span,
                        },
                        span,
                    ),
                    *body,
                    *self._skip_on_break(
                        [_assign(idx, _binary_int("+", _name(idx, int_t, span), _const_int(1, span), span), span)],
                        span,
                    ),
                ],
                span,
                init=False,
            ),
        ]

    def _loop_body(self, stmt: ast.For) -> list[dict[str, Any]]:
        if self.comp_elt is None or self.comp_dest is None:
            return [*self._continue_reset(span_of(stmt, self.filename)), *self._body(list(stmt.body))]
        if self.comp_gens:
            # A later `for` descends from this level. This level's `if`s gate
            # the descent itself; the next level's `if`s wait for its body.
            nxt = self.comp_gens[0]
            self.comp_gens = self.comp_gens[1:]
            inner_for = ast.For(target=nxt.target, iter=nxt.iter, body=[ast.Pass()], orelse=[], type_comment=None)
            ast.copy_location(inner_for, nxt.iter)
            stmts: list[ast.stmt] = [inner_for]
            ifs = self.comp_ifs
            self.comp_ifs = nxt.ifs
            for cond in reversed(ifs):
                guard = ast.If(test=cond, body=stmts, orelse=[])
                ast.copy_location(guard, cond)
                stmts = [guard]
            out: list[dict[str, Any]] = []
            for item in stmts:
                out.extend(self._stmt(item))
            return out
        saved = self.prelude
        self.prelude = []
        try:
            key_node = self.comp_key
            key = self._expr(key_node) if key_node is not None else None
            elt = self._expr(self.comp_elt)
            inner = self.prelude
        finally:
            self.prelude = saved
        elem = elt["type"]
        if self.comp_kind == "dict":
            if key_node is None or key is None:
                self.fail(self.comp_elt, "PS2002", "dict comprehension is not lowered")
            key_t = key["type"]
            if key_t["kind"] not in {"int", "str"}:
                self.fail(key_node, "PS2002", "dict keys are int or str")
            if elem["kind"] == "bool":
                self.fail(self.comp_elt, "PS2002", "dict values of bool are not lowered")
            typ = {"kind": "dict", "key": key_t, "value": elem}
            what = "dict"
        elif self.comp_kind == "set":
            if elem["kind"] not in {"int", "str"}:
                self.fail(self.comp_elt, "PS2002", "set elements are int or str")
            typ = {"kind": "list", "elem": elem, "set": True}
            what = "set"
        else:
            if elem["kind"] == "bool" or elem["kind"] not in {"int", "float", "str", "bytes", "list", "dict", "class"}:
                self.fail(self.comp_elt, "PS2002", f"list[{_show(elem)}] is not lowered")
            typ = {"kind": "list", "elem": elem}
            what = "list"
        if self.comp_expect is not None and self.comp_expect["kind"] == typ["kind"]:
            self._expect(self.comp_elt, typ, self.comp_expect, what)
        if not self.comp_ready:
            self.locals[self.comp_dest] = typ
            span = span_of(self.comp_elt, self.filename)
            empty = (
                {"kind": "dict", "pairs": [], "type": typ, "span": span}
                if self.comp_kind == "dict"
                else {"kind": "list", "elems": [], "type": typ, "span": span}
            )
            self.comp_init = _assign(self.comp_dest, empty, span)
            self.comp_ready = True
        # The push args are emitted where they sit, so key/element calls are
        # stored first: each runs once, and only inside this branch.
        kept: list[dict[str, Any]] = []
        if self.comp_kind == "dict":
            kslot = self._temp("key")
            vslot = self._temp("elt")
            self.locals[kslot] = key["type"]
            self.locals[vslot] = elem
            kept = [
                *inner,
                _assign(kslot, key, key["span"]),
                _assign(vslot, elt, elt["span"]),
                {
                    "kind": "setitem",
                    "container": _name(self.comp_dest, typ, elt["span"]),
                    "index": _name(kslot, key["type"], elt["span"]),
                    "value": _name(vslot, elem, elt["span"]),
                    "span": elt["span"],
                },
            ]
        elif self.comp_kind == "set":
            kept = [*inner, *self._set_insert_stmts(self.comp_dest, typ, elt, elt["span"])]
        else:
            slot = self._temp("elt")
            self.locals[slot] = elem
            push = {
                "kind": "expr",
                "expr": {
                    "kind": "call",
                    "callee": {"kind": "module", "module": "builtins", "name": "list.append"},
                    "args": [_name(self.comp_dest, typ, elt["span"]), _name(slot, elem, elt["span"])],
                    "symbol": "ps_list_push",
                    "type": {"kind": "none"},
                    "span": elt["span"],
                },
                "span": elt["span"],
            }
            kept = [*inner, _assign(slot, elt, elt["span"]), push]
        for cond in reversed(self.comp_ifs):
            saved_cond = self.prelude
            self.prelude = []
            try:
                filt = self._bool_cond(cond)
                filt_pre = self.prelude
            finally:
                self.prelude = saved_cond
            kept = [
                *filt_pre,
                {"kind": "if", "cond": filt, "then": kept, "else": [], "span": span_of(cond, self.filename)},
            ]
        return kept

    def _with_as_file(self, stmt: ast.With, item: ast.withitem, span: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(item.optional_vars, ast.Name):
            self.fail(stmt, "PS2002", "with as_file needs a name")
        ctx = item.context_expr
        assert isinstance(ctx, ast.Call)
        if len(ctx.args) != 1 or ctx.keywords:
            self.fail(ctx, "PS2003", "as_file expects 1 argument(s)")
        opened = self._expr(ctx.args[0])
        if opened["type"]["kind"] != "class" or opened["type"].get("name") != "Path":
            self.fail(ctx, "PS1003", "as_file takes a resource path")
        target = item.optional_vars.id
        if target in self.bindings or target in self.constants:
            self.fail(item.optional_vars, "PS2002", f"{target} is already bound")
        self.locals[target] = opened["type"]
        return [_assign(target, opened, span), *self._body(list(stmt.body))]

    def _with_cm(
        self, stmt: ast.With, item: ast.withitem, ctx_v: dict[str, Any], span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """`with CM() as c`: __enter__ binds the target, __exit__ runs on both
        the normal and the raised path, and a truthy __exit__ suppresses the
        in-flight exception (CPython's (None, None, None) is the three empty
        optionals here).

        Subset signature: `__exit__(self, Optional[str], Optional[Exception],
        Optional[str]) -> bool` — the exception's class name, the exception,
        and a traceback stand-in that is always empty.
        """
        class_name = ctx_v["type"]["name"]
        info = self.classes[class_name]
        exit_sig = info["methods"].get("__exit__")
        if exit_sig is None or info.get("method_kind", {}).get("__exit__", "method") != "method":
            self.fail(stmt, "PS2001", f"{class_name}.__exit__ is not a method")
        params = exit_sig[0]
        if len(params) != 4 or [param["type"].get("name") for param in params[1:]] != ["opt_str", "opt_exc", "opt_str"]:
            self.fail(
                stmt, "PS2003", f"{class_name}.__exit__ takes (Optional[str], Optional[Exception], Optional[str])"
            )
        target = item.optional_vars
        if target is not None and not isinstance(target, ast.Name):
            self.fail(stmt, "PS2002", "with-as needs a name")
        mgr = self._temp("mgr")
        self.locals[mgr] = ctx_v["type"]
        mgr_n = _name(mgr, ctx_v["type"], span)
        opt_str = self._opt_type({"kind": "str"}, stmt)
        opt_exc = self._opt_type({"kind": "exc"}, stmt)
        exc_slot = self._temp("exc")
        self.locals[exc_slot] = {"kind": "exc"}
        exc_n = _name(exc_slot, {"kind": "exc"}, span)

        def box(typ: dict[str, Any], value: dict[str, Any] | None) -> tuple[str, list[dict[str, Any]]]:
            temp = self._temp("opt")
            self.locals[temp] = typ
            made = {
                "kind": "call",
                "callee": {"kind": "class", "name": typ["name"]},
                "args": [],
                "type": typ,
                "span": span,
            }
            stmts = [_assign(temp, made, span)]
            if value is not None:
                stmts += [
                    {
                        "kind": "setattr",
                        "object": _name(temp, typ, span),
                        "name": "_tag",
                        "value": _const_bool(True, span),
                        "span": span,
                    },
                    {"kind": "setattr", "object": _name(temp, typ, span), "name": "_val", "value": value, "span": span},
                ]
            return temp, stmts

        et_n, et_stmts = box(
            opt_str, self._abi_call("builtins", "exc.name", [exc_n], {"kind": "str"}, "ps_exc_class", span)
        )
        ev_n, ev_stmts = box(opt_exc, exc_n)
        tb_n, tb_stmts = box(opt_str, None)
        suppressed = self._temp("supp")
        self.locals[suppressed] = {"kind": "bool"}
        raised_exit = self._dunder_call(
            stmt,
            mgr_n,
            "__exit__",
            [_name(et_n, opt_str, span), _name(ev_n, opt_exc, span), _name(tb_n, opt_str, span)],
            span,
        )
        et0, et0_stmts = box(opt_str, None)
        ev0, ev0_stmts = box(opt_exc, None)
        tb0, tb0_stmts = box(opt_str, None)
        clean_exit = self._dunder_call(
            stmt,
            mgr_n,
            "__exit__",
            [_name(et0, opt_str, span), _name(ev0, opt_exc, span), _name(tb0, opt_str, span)],
            span,
        )
        enter = self._dunder_call(stmt, mgr_n, "__enter__", [], span)
        out = [_assign(mgr, ctx_v, span)]
        if target is None:
            out.append({"kind": "expr", "expr": enter, "span": span})
        else:
            self.locals[target.id] = enter["type"]
            out.append(_assign(target.id, enter, span))
        body = self._body(list(stmt.body))
        out.append(
            {
                "kind": "try",
                "body": [*et0_stmts, *ev0_stmts, *tb0_stmts, *body, {"kind": "expr", "expr": clean_exit, "span": span}],
                "handlers": [
                    {
                        "exc": ["Exception"],
                        "body": [
                            *et_stmts,
                            *ev_stmts,
                            *tb_stmts,
                            _assign(suppressed, raised_exit, span),
                            {
                                "kind": "if",
                                "cond": _not_bool(suppressed, span),
                                "then": [
                                    {
                                        "kind": "raise",
                                        "exc": "Exception",
                                        "reraise": True,
                                        "excslot": exc_slot,
                                        "span": span,
                                    }
                                ],
                                "else": [],
                                "span": span,
                            },
                        ],
                        "excslot": exc_slot,
                        "bind": exc_slot,
                    }
                ],
                "span": span,
            }
        )
        return out

    def _with_open(self, stmt: ast.With, item: ast.withitem, span: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(item.optional_vars, ast.Name):
            self.fail(stmt, "PS2002", "with open needs a name")
        assert isinstance(item.context_expr, ast.Call)
        opened = self._expr(item.context_expr)
        target = item.optional_vars.id
        if target in self.bindings or target in self.constants:
            self.fail(item.optional_vars, "PS2002", f"{target} is already bound")
        self.locals[target] = opened["type"]
        close = {
            "kind": "expr",
            "expr": self._abi_call(
                "io", "close", [_name(target, opened["type"], span)], {"kind": "none"}, "ps_io_close", span
            ),
            "span": span,
        }
        body = self._inject_close(self._body(list(stmt.body)), close)
        return [
            _assign(target, opened, span),
            {
                "kind": "try",
                "body": [*body, close],
                "handlers": [
                    {
                        "exc": ["Exception"],
                        "body": [
                            close,
                            {
                                "kind": "raise",
                                "exc": "Exception",
                                "message": None,
                                "reraise": True,
                                "span": span,
                            },
                        ],
                    }
                ],
                "span": span,
            },
        ]

    def _with_io(
        self, stmt: ast.With, item: ast.withitem, ctx_v: dict[str, Any], span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """`with f`/`with f as g` on a TextIO/BytesIO receiver: the file is its
        own context manager — `as` rebinds the same handle, and both the
        normal and the raised path close it (CPython io.__exit__ closes).
        """
        target = item.optional_vars
        if target is not None and not isinstance(target, ast.Name):
            self.fail(stmt, "PS2002", "with-as needs a name")
        hold = self._temp("io")
        self.locals[hold] = ctx_v["type"]
        hold_n = _name(hold, ctx_v["type"], span)
        out = [_assign(hold, ctx_v, span)]
        if target is not None:
            if target.id in self.bindings or target.id in self.constants:
                self.fail(target, "PS2002", f"{target.id} is already bound")
            self.locals[target.id] = ctx_v["type"]
            out.append(_assign(target.id, hold_n, span))
        close = {
            "kind": "expr",
            "expr": self._abi_call("io", "close", [hold_n], {"kind": "none"}, "ps_io_close", span),
            "span": span,
        }
        body = self._inject_close(self._body(list(stmt.body)), close)
        out.append(
            {
                "kind": "try",
                "body": [*body, close],
                "handlers": [
                    {
                        "exc": ["Exception"],
                        "body": [
                            close,
                            {
                                "kind": "raise",
                                "exc": "Exception",
                                "message": None,
                                "reraise": True,
                                "span": span,
                            },
                        ],
                    }
                ],
                "span": span,
            }
        )
        return out

    def _for_enumerate(self, stmt: ast.For, span: dict[str, Any]) -> list[dict[str, Any]]:
        call = stmt.iter
        assert isinstance(call, ast.Call)
        if call.keywords:
            self.fail(call, "PS2003", "keyword arguments are not lowered")
        if len(call.args) not in {1, 2}:
            self.fail(call, "PS2003", "enumerate takes 1 or 2 arguments")
        source = self._expr(call.args[0])
        if source["type"]["kind"] != "list" or source["type"].get("set"):
            self.fail(call, "PS2002", "enumerate() takes a list")
        elem = source["type"]["elem"]
        if elem["kind"] not in _SCALAR | {"bool"}:
            self.fail(call, "PS2002", f"enumerate() of list[{_show(elem)}] is not lowered")
        start = _const_int(0, span)
        if len(call.args) == 2:
            start = self._expr(call.args[1], {"kind": "int"})
            if start["type"]["kind"] != "int":
                self.fail(call, "PS1003", "enumerate start is int")
        return self._for_pairs(stmt, span, [source], [elem], start, "enumerate")

    def _for_zip(self, stmt: ast.For, span: dict[str, Any]) -> list[dict[str, Any]]:
        call = stmt.iter
        assert isinstance(call, ast.Call)
        if call.keywords or len(call.args) != 2:
            self.fail(call, "PS2003", "zip takes 2 lists")
        sources = [self._expr(arg) for arg in call.args]
        elems: list[dict[str, Any]] = []
        for source in sources:
            if source["type"]["kind"] != "list" or source["type"].get("set"):
                self.fail(call, "PS2002", "zip() takes lists")
            elem = source["type"]["elem"]
            if elem["kind"] not in _SCALAR | {"bool"}:
                self.fail(call, "PS2002", f"zip() of list[{_show(elem)}] is not lowered")
            elems.append(elem)
        return self._for_pairs(stmt, span, sources, elems, None, "zip")

    def _for_pairs(
        self,
        stmt: ast.For,
        span: dict[str, Any],
        sources: list[dict[str, Any]],
        elems: list[dict[str, Any]],
        enum_start: dict[str, Any] | None,
        what: str,
    ) -> list[dict[str, Any]]:
        int_t = {"kind": "int"}
        fields = ([{"kind": "int"}, *elems]) if enum_start is not None else elems
        unpack = not isinstance(stmt.target, ast.Name)
        pair: dict[str, Any] | None = None
        target = ""
        fresh = False
        if not unpack:
            pair = self._tuple_type(fields, stmt.iter)
            target = stmt.target.id
            fresh = self._bind_loop_target(target, pair, stmt.target)
        seqs = [self._temp("seq") for _ in sources]
        idx = self._temp("idx")
        length = self._temp("len")
        enum_i = self._temp("enum")
        for seq, source in zip(seqs, sources, strict=True):
            self.locals[seq] = source["type"]
        self.locals[idx] = int_t
        self.locals[length] = int_t
        if enum_start is not None:
            self.locals[enum_i] = int_t
        values: list[dict[str, Any]] = []
        if enum_start is not None:
            values.append(_name(enum_i, int_t, span))
        for seq, source, elem in zip(seqs, sources, elems, strict=True):
            values.append(
                {
                    "kind": "subscript",
                    "value": _name(seq, source["type"], span),
                    "index": _name(idx, int_t, span),
                    "type": elem,
                    "span": span,
                }
            )
        if unpack:
            bind = self._for_unpack(stmt, fields, values, span)
        else:
            bind = _tuple_assign(target, pair, values, span)
        body = self._loop_body(stmt)
        tail = [_assign(idx, _binary_int("+", _name(idx, int_t, span), _const_int(1, span), span), span)]
        if enum_start is not None:
            tail.append(_assign(enum_i, _binary_int("+", _name(enum_i, int_t, span), _const_int(1, span), span), span))
        step = [*bind, *body, *self._skip_on_break(tail, span)]
        prefix: list[dict[str, Any]] = []
        if fresh and pair is not None:
            zeros = [_zero(field, span) for field in fields]
            prefix.extend(_tuple_assign(target, pair, zeros, span))
        for seq, source in zip(seqs, sources, strict=True):
            prefix.append(_assign(seq, source, span))
        prefix.append(_assign(idx, _const_int(0, span), span))
        if len(sources) == 1:
            prefix.append(_assign(length, _len_call(_name(seqs[0], sources[0]["type"], span), span), span))
        else:
            left_n = self._temp("n")
            right_n = self._temp("n")
            self.locals[left_n] = int_t
            self.locals[right_n] = int_t
            prefix.extend(
                [
                    _assign(left_n, _len_call(_name(seqs[0], sources[0]["type"], span), span), span),
                    _assign(right_n, _len_call(_name(seqs[1], sources[1]["type"], span), span), span),
                    _assign(length, _name(left_n, int_t, span), span),
                    {
                        "kind": "if",
                        "cond": _compare("<", _name(right_n, int_t, span), _name(left_n, int_t, span), span),
                        "then": [_assign(length, _name(right_n, int_t, span), span)],
                        "else": [],
                        "span": span,
                    },
                ]
            )
        if enum_start is not None:
            prefix.append(_assign(enum_i, enum_start, span))
        prefix.extend(
            self._user_while(
                _compare("<", _name(idx, int_t, span), _name(length, int_t, span), span),
                step,
                span,
            )
        )
        if what not in {"enumerate", "zip"}:
            self.fail(stmt, "PS2002", f"{what} is not lowered")
        return prefix

    def _for_unpack(
        self,
        stmt: ast.For,
        fields: list[dict[str, Any]],
        values: list[dict[str, Any]],
        span: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Bind a tuple/list for-target's names to one iterated item's fields.

        Fresh names zero before the loop via prelude (a post-loop read of an
        uniterated unpack target reads as zero, same as a name target). A
        wrong name count still binds each name — padding extras with int — so
        the body lowers, and the step raises the CPython unpack ValueError,
        which fires only when the iterable is non-empty.
        """
        names = [elt.id for elt in stmt.target.elts]
        for index, name in enumerate(names):
            typ = fields[index] if index < len(fields) else {"kind": "int"}
            if self._bind_loop_target(name, typ, stmt.target):
                self.prelude.append(_assign(name, _zero(typ, span), span))
        if len(names) != len(fields):
            if len(fields) > len(names):
                text = f"too many values to unpack (expected {len(names)})"
            else:
                text = f"not enough values to unpack (expected {len(names)}, got {len(fields)})"
            return [_raise_value(_const_str(text, span), span)]
        return [_assign(name, value, span) for name, value in zip(names, values, strict=True)]

    def _inject_close(self, body: list[dict[str, Any]], close: dict[str, Any]) -> list[dict[str, Any]]:
        """Close after a return value is computed, so ``return handle.read()`` still sees an open file."""
        out: list[dict[str, Any]] = []
        for stmt in body:
            if stmt["kind"] == "return":
                value = stmt["value"]
                if value["kind"] == "const" and value["type"]["kind"] == "none":
                    out.append(close)
                    out.append(stmt)
                    continue
                temp = self._temp("withret")
                self.locals[temp] = value["type"]
                out.append(_assign(temp, value, value["span"]))
                out.append(close)
                out.append({**stmt, "value": _name(temp, value["type"], value["span"])})
                continue
            if stmt["kind"] == "if":
                out.append(
                    {
                        **stmt,
                        "then": self._inject_close(stmt["then"], close),
                        "else": self._inject_close(stmt["else"], close),
                    }
                )
                continue
            if stmt["kind"] == "while":
                out.append({**stmt, "body": self._inject_close(stmt["body"], close)})
                continue
            if stmt["kind"] == "try":
                out.append(
                    {
                        **stmt,
                        "body": self._inject_close(stmt["body"], close),
                        "handlers": [
                            {**handler, "body": self._inject_close(handler["body"], close)}
                            for handler in stmt["handlers"]
                        ],
                    }
                )
                continue
            out.append(stmt)
        return out


def _is_call_name(node: ast.expr, name: str) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name


def _is_docstring(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def _is_main_guard(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.If) or not isinstance(stmt.test, ast.Compare):
        return False
    test = stmt.test
    return (
        isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
    )


def _is_range_call(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "range"


def _is_setitem(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Subscript)


def _handler_names(lowerer: Lowerer, node: ast.expr) -> list[str]:
    names = node.elts if isinstance(node, ast.Tuple) else [node]
    found: list[str] = []
    for item in names:
        if isinstance(item, ast.Attribute):
            # A dotted spelling (urllib.error.HTTPError): resolve through the
            # module chain and alias onto the synthetic class.
            resolved_attr = lowerer._resolve_module(item)
            if resolved_attr is None or resolved_attr[3] != "lowered":
                lowerer.fail(item, "PS2002", "except names a lowered exception")
            alias = alias_name(resolved_attr[1], resolved_attr[2])
            if alias is None:
                lowerer.fail(item, "PS2002", "except names a lowered exception")
            found.append(alias)
            continue
        if not isinstance(item, ast.Name):
            lowerer.fail(item, "PS2002", "except names a lowered exception")
        resolved = lowerer.class_aliases.get(item.id, item.id)
        info = lowerer.classes.get(resolved)
        user_exception = info is not None and bool(info.get("exception"))
        if (
            resolved not in EXCEPTIONS
            and item.id not in EXCEPTIONS
            and resolved not in RESERVED_EXCEPTIONS
            and item.id not in RESERVED_EXCEPTIONS
            and not user_exception
        ):
            lowerer.fail(item, "PS2002", "except names a lowered exception")
        found.append(resolved if user_exception else item.id)
    return found


def _not_bool(name: str, span: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "unary",
        "op": "not",
        "operand": _name(name, {"kind": "bool"}, span),
        "type": {"kind": "bool"},
        "span": span,
    }


def _stmt_jumps_this_loop(stmt: ast.stmt, jump: type[ast.stmt]) -> bool:
    if isinstance(stmt, jump):
        return True
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return False
    nested: list[ast.stmt] = []
    if isinstance(stmt, (ast.While, ast.For)):
        # A jump in the body targets the nested loop; a jump in its else
        # block runs after the loop and targets the enclosing one.
        nested = list(stmt.orelse)
    elif isinstance(stmt, ast.If):
        nested = [*stmt.body, *stmt.orelse]
    elif isinstance(stmt, ast.Try):
        nested = [*stmt.body, *stmt.orelse, *stmt.finalbody]
        for handler in stmt.handlers:
            nested.extend(handler.body)
    elif isinstance(stmt, ast.With):
        nested = list(stmt.body)
    elif isinstance(stmt, ast.Match):
        nested = [child for case in stmt.cases for child in case.body]
    return any(_stmt_jumps_this_loop(child, jump) for child in nested)


def _stmt_breaks_this_loop(stmt: ast.stmt) -> bool:
    return _stmt_jumps_this_loop(stmt, ast.Break)


def _stmt_continues_this_loop(stmt: ast.stmt) -> bool:
    return _stmt_jumps_this_loop(stmt, ast.Continue)


def _always_returns(body: list[dict[str, Any]]) -> bool:
    for stmt in body:
        if stmt["kind"] == "return" or stmt["kind"] == "raise":
            return True
        if stmt["kind"] == "if" and _always_returns(stmt["then"]) and _always_returns(stmt["else"]):
            return True
        if (
            stmt["kind"] == "try"
            and _always_returns(stmt["body"])
            and all(_always_returns(handler["body"]) for handler in stmt["handlers"])
        ):
            return True
    return False
