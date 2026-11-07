"""Statement lowering for the lowerer.

Split out of `__init__.py` as E0 move 3. `Lowerer` composes this mixin in.
The area owns the `_stmt` dispatch and its statement forms: assignments
(plain, annotated, augmented, global slots), `if`/`while`/`for` (range,
repeat, chain, enumerate, zip, list), `break`/`continue` plumbing,
`return`/`raise`/`assert`/`del`, `try` handlers, `with` (suppress,
nullcontext, `open`, `importlib.resources.as_file`), and `match` on
literal, wildcard, capture, or, guarded, and `None` patterns.

The assert/raise/try/with and for families moved verbatim to
`lower._stmt_b` (`StmtBMixin`), and the narrow/unpack family plus the
module-level helpers moved verbatim to `lower._stmt_a` (`StmtAMixin`):
`StmtMixin` composes both, so every method stays reachable as
`self._method()` and every helper unqualified, so the move is
behavior-identical. Loop-flag plumbing and close injection are
statement-shaped and moved with the area. Small leaf helpers shared across
the package (tables, `_assign`, scalar constructors) stay in `_core.py`.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any  # noqa: F401  # moved-verbatim import

from pyscriptc import _irnode as _ir  # noqa: F401  # moved-verbatim import
from pyscriptc.errors import span_of  # noqa: F401  # moved-verbatim import
from pyscriptc.ffi_manifest import is_handle_type  # noqa: F401  # moved-verbatim import
from pyscriptc.lower._core import (
    Fence,  # noqa: F401  # moved-verbatim import
    _any_contains,  # noqa: F401  # moved-verbatim import
    _assign,  # noqa: F401  # moved-verbatim import
    _const_bool,  # noqa: F401  # moved-verbatim import
    _const_none,  # noqa: F401  # moved-verbatim import
    _has_typevar,  # noqa: F401  # moved-verbatim import
    _maybe_type_kind,  # noqa: F401  # moved-verbatim import
    _node_kind,  # noqa: F401  # moved-verbatim import
    _node_type_kind,  # noqa: F401  # moved-verbatim import
    _type_kind,  # noqa: F401  # moved-verbatim import
    _zero,  # noqa: F401  # moved-verbatim import
)
from pyscriptc.lower._stmt_a import (
    StmtAMixin,  # noqa: F401  # moved-verbatim import
    _always_returns,  # noqa: F401  # re-exported for lower.__init__ and the sibling mixins
    _expr_wrap,  # noqa: F401  # moved-verbatim import
    _if_stmt,  # noqa: F401  # moved-verbatim import
    _is_main_guard,  # noqa: F401  # re-exported for lower.__init__
    _mk_span_of,  # noqa: F401  # moved-verbatim import
    _not_bool,  # noqa: F401  # moved-verbatim import
    _pass_stmt,  # noqa: F401  # moved-verbatim import
    _while_loop,  # noqa: F401  # moved-verbatim import
)
from pyscriptc.lower._stmt_b import (
    StmtBMixin,  # noqa: F401  # moved-verbatim import
    _is_docstring,  # noqa: F401  # re-exported for lower.__init__ and _classes
)
from pyscriptc.tirserde import dict_to_node, node_to_dict  # noqa: F401  # moved-verbatim import

if TYPE_CHECKING:
    from pyscriptc.lower import Lowerer  # noqa: F401  # moved-verbatim import


def _is_setitem(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Subscript)


def _ast_exits(body: list[ast.stmt]) -> bool:
    """The statement list never falls through: it returns, raises, continues, breaks or exits the process."""
    for stmt in body:
        if isinstance(stmt, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
            return True
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            func = stmt.value.func
            if isinstance(func, ast.Attribute) and func.attr == "exit" and isinstance(func.value, ast.Name):
                if func.value.id == "sys":
                    return True
            if isinstance(func, ast.Name) and func.id in {"exit", "quit"}:
                return True
        if isinstance(stmt, ast.If) and stmt.orelse and _ast_exits(stmt.body) and _ast_exits(stmt.orelse):
            return True
    return False


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


class StmtMixin(StmtAMixin, StmtBMixin):
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
            guarded = [_if_stmt(_not_bool(self.continue_flag, span), guarded, span)]
        if self.break_flag is not None:
            guarded = [_if_stmt(_not_bool(self.break_flag, span), guarded, span)]
        return guarded[0]

    def _break_init(self, span: dict[str, Any]) -> list[dict[str, Any]]:
        if self.break_flag is None:
            return []
        return [_assign(self.break_flag, _const_bool(False, span), span)]

    def _skip_on_break(self, stmts: list[dict[str, Any]], span: dict[str, Any]) -> list[dict[str, Any]]:
        if self.break_flag is None:
            return stmts
        return [_if_stmt(_not_bool(self.break_flag, span), stmts, span)]

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
            return [_while_loop(cond, body, span)]
        own_flag = False
        if flag is None:
            flag = self._temp("whl")
            self.locals[flag] = {"kind": "bool"}
            own_flag = True
        loop = _while_loop(
            _not_bool(flag, span),
            [
                *pre,
                _if_stmt(cond, body, span, [_assign(flag, _const_bool(True, span), span)]),
            ],
            span,
        )
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
            return [_pass_stmt(span)]
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
                if not (_maybe_type_kind(typ) == "class" and typ.get("name") == "Deque" and "elem" not in typ):
                    # A bare `collections.deque` annotation carries no element
                    # type; binding it would send a later `.append` into the
                    # synthetic-method path with no `elem` to check against.
                    self.locals[stmt.target.id] = typ
                raise
            self._expect(stmt, value["type"], typ, stmt.target.id)
            if _type_kind(typ) == "any" or (
                _type_kind(typ) in {"dict", "list"}
                and _any_contains(typ)
                and _node_type_kind(value) in {"dict", "list"}
                and not _any_contains(value["type"])
            ):
                # Issue #781: a concrete initializer satisfies an Any
                # container annotation; the annotation's shape (the key
                # type, the element slots) is the container the IR keeps,
                # and the reads resolve the element from the value facts.
                typ = value["type"]
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
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Attribute):
            # `self.f: T = v` declares the field and stores like an
            # assignment; a bare `self.f: T` is annotation-only, no store.
            if stmt.value is None:
                return [_pass_stmt(span)]
            return [self._setattr_target(stmt.target, stmt.value, stmt, span)]
        if isinstance(stmt, ast.Global):
            for name in stmt.names:
                slot = self.global_slots.get(name)
                if slot is None or not slot.get("active"):
                    self.fail(stmt, "PS2002", f"global {name} is not a module slot")
            return [_pass_stmt(span)]
        if isinstance(stmt, ast.Assign):
            if (
                len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and (nt_call := self._namedtuple_call(stmt.value)) is not None
            ):
                self._namedtuple_decl(stmt.targets[0].id, nt_call, stmt)
                return [_pass_stmt(span)]
            if (
                len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and (tv_call := self._typevar_call(stmt.value)) is not None
            ):
                self._declare_typevar(stmt.targets[0].id, tv_call, stmt)
                return [_pass_stmt(span)]
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
                    if _node_type_kind(value) == "func":
                        self.locals[target] = value["type"]
                        return [_assign(target, value, span)]
                    # boxed:begin
                    if value["type"].get("island"):
                        # An island call yields an opaque handle; the name owns
                        # it and the scope-exit free releases it (issue #553).
                        self.locals[target] = value["type"]
                        self._own_handle(target, value["type"])
                        return [_assign(target, value, span)]
                    # boxed:end
                    if _node_type_kind(value) == "class" and value["type"].get("name") in {"ArgParser", "Namespace"}:
                        self.locals[target] = value["type"]
                        return [_assign(target, value, span)]
                    if self._bridge_type(stmt.value) is not None:
                        # Checker bridge: the checker proved the call's result
                        # type; bind the name to it instead of fencing.
                        value = self._expr(stmt.value)
                        if _node_type_kind(value) != "func":
                            self.locals[target] = value["type"]
                            return [_assign(target, value, span)]
                if self._bridge_type(stmt.value) is not None:
                    # Checker bridge: an unannotated assignment whose inferred
                    # type the subset can express binds without an annotation.
                    value = self._expr(stmt.value)
                    self.locals[target] = value["type"]
                    self._note_frozen(target, stmt.value)
                    return [_assign(target, value, span)]
                # The inference peek (issue #500): a literal, a comprehension
                # or a call of known return type types the name from its
                # value. The peek discards the speculative IR; the real pass
                # below emits the value once. Anything the peek refuses keeps
                # the fence, and a bare `None` needs an Optional (#413).
                # An empty display is ambiguous until a use says otherwise:
                # the accumulated-type pass (below) reads the appends and
                # item writes in the rest of the body, and only a proven
                # single element type binds the name.
                accumulated = self._infer_accumulated_type(stmt)
                if accumulated is not None:
                    self.locals[target] = accumulated
                    value = self._expr(stmt.value, accumulated)
                    self._expect(stmt, value["type"], accumulated, target)
                    dropped = self._rebind_handle(target, accumulated, stmt.value, span)
                    self._note_frozen(target, stmt.value)
                    return [*dropped, _assign(target, value, span)]
                inferred = self._infer_type_known(stmt.value) or self._infer_type_of(stmt.value)
                if inferred is not None and _type_kind(inferred) != "none":
                    self.locals[target] = inferred
                    value = self._expr(stmt.value, inferred)
                    self._expect(stmt, value["type"], inferred, target)
                    dropped = self._rebind_handle(target, inferred, stmt.value, span)
                    self._note_frozen(target, stmt.value)
                    return [*dropped, _assign(target, value, span)]
                self._infer_fence(stmt, target)
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
            # The test narrows tagged optionals per branch (`x is not None`,
            # `x`, `not x`, and `and`/`or` chains of those): reads of a
            # narrowed name lower to its `_val`. A name rebound anywhere
            # inside that branch is not narrowed.
            when_true, when_false = self._narrow_names(stmt.test)
            then_body = self._narrowed(when_true, stmt.body, lambda: self._body(list(stmt.body)))
            else_body = self._narrowed(when_false, stmt.orelse, lambda: self._body(list(stmt.orelse)))
            return [_if_stmt(self._bool_cond_of(stmt.test, {"kind": "bool"}), then_body, span, else_body)]
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
                when_true, _ = self._narrow_names(stmt.test)
                inner = self._narrowed(when_true, stmt.body, lambda: self._body(list(stmt.body)))
                body = [*self._continue_reset(span), *inner]
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
                    _if_stmt(_not_bool(broke, span), else_body, span),
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
                    return [
                        node_to_dict(_ir.return_stmt(dict_to_node(self._opt_box(None, ret, span)), _mk_span_of(span)))
                    ]
                if _type_kind(ret) != "none":
                    self.fail(stmt, "PS1003", "bare return is None, and this function does not return None")
                return [node_to_dict(_ir.return_stmt(dict_to_node(_const_none(span)), _mk_span_of(span)))]
            value = self._expr(stmt.value, ret)
            self._expect(stmt, value["type"], ret, "return")
            # A returned handle transfers ownership to the caller: the scope
            # exit must not free it too (issue #224).
            if _node_kind(value) == "name" and is_handle_type(value["type"]):
                self.returned_handles.add(value["id"])
            return [node_to_dict(_ir.return_stmt(dict_to_node(value), _mk_span_of(span)))]
        if isinstance(stmt, ast.Expr):
            return [_expr_wrap(self._expr(stmt.value), span)]
        if isinstance(stmt, ast.Raise):
            return [self._raise(stmt, span)]
        if isinstance(stmt, ast.Pass):
            return [{"kind": "pass", "span": span}]
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
        if isinstance(stmt, ast.TypeAlias) and isinstance(stmt.name, ast.Name):
            self.fail(
                stmt,
                "PS2002",
                f"PEP 695 type alias {stmt.name.id} is not lowered; annotate with the concrete type",
            )
        self.fail(stmt, "PS2002", f"{type(stmt).__name__} is not lowered")
        raise AssertionError("unreachable")

    def _current_method(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if self.function and "." in self.function:
            class_name, method = self.function.split(".", 1)
            return self.classes[class_name]["methods"][method]
        self.fail(ast.Pass(), "PS2002", "return is outside a function")
        raise AssertionError("unreachable")
