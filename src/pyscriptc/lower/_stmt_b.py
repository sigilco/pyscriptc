"""Statement lowering for the exception, try, with, and for families.

Split verbatim from `lower._stmt` (E0 move for #852). `StmtBMixin` holds
the statement forms that moved out of `StmtMixin`: `assert`, `raise` (the
chained-exception message builders included), `try` handlers, `with`
(suppress, nullcontext, `open`, `importlib.resources.as_file`), and the
`for` family (range, repeat, chain, enumerate, zip, pairs, unpack, list
loops, and the shared loop-body builder). `StmtMixin` composes it the way
`StdlibDispatchMixin` composes `StdlibBindMixin`, so every method stays
reachable as `self._assert(...)` and friends. Every body below is the code
that lived in `lower._stmt`, unchanged: the IR and diagnostics are
byte-identical.
"""

from __future__ import annotations

import ast
import copy
from typing import TYPE_CHECKING, Any  # noqa: F401  # moved-verbatim import

from pyscriptc import _irnode as _ir  # noqa: F401  # moved-verbatim import
from pyscriptc.builtins import EXCEPTIONS, RESERVED_EXCEPTIONS  # noqa: F401  # moved-verbatim import
from pyscriptc.errors import span_of  # noqa: F401  # moved-verbatim import
from pyscriptc.ir import types_equal  # noqa: F401  # moved-verbatim import
from pyscriptc.lower._core import (
    _SCALAR,  # noqa: F401  # moved-verbatim import
    _assign,  # noqa: F401  # moved-verbatim import
    _attr,  # noqa: F401  # moved-verbatim import
    _binary_int,  # noqa: F401  # moved-verbatim import
    _compare,  # noqa: F401  # moved-verbatim import
    _const_bool,  # noqa: F401  # moved-verbatim import
    _const_int,  # noqa: F401  # moved-verbatim import
    _const_str,  # noqa: F401  # moved-verbatim import
    _item,  # noqa: F401  # moved-verbatim import
    _len_call,  # noqa: F401  # moved-verbatim import
    _maybe_type_kind,  # noqa: F401  # moved-verbatim import
    _name,  # noqa: F401  # moved-verbatim import
    _node_kind,  # noqa: F401  # moved-verbatim import
    _node_type_kind,  # noqa: F401  # moved-verbatim import
    _raise_value,  # noqa: F401  # moved-verbatim import
    _show,  # noqa: F401  # moved-verbatim import
    _tuple_assign,  # noqa: F401  # moved-verbatim import
    _type_kind,  # noqa: F401  # moved-verbatim import
    _zero,  # noqa: F401  # moved-verbatim import
)
from pyscriptc.lower._stmt_a import (
    _body_nodes,  # noqa: F401  # moved-verbatim import
    _expr_wrap,  # noqa: F401  # moved-verbatim import
    _if_stmt,  # noqa: F401  # moved-verbatim import
    _mk_span_of,  # noqa: F401  # moved-verbatim import
    _not_bool,  # noqa: F401  # moved-verbatim import
    _unary_not,  # noqa: F401  # moved-verbatim import
    dict_to_list,  # noqa: F401  # moved-verbatim import
)
from pyscriptc.reflection import kwargs_call_note
from pyscriptc.restlower import alias_name  # noqa: F401  # moved-verbatim import
from pyscriptc.tirserde import dict_to_node, dict_to_type, node_to_dict  # noqa: F401  # moved-verbatim import

if TYPE_CHECKING:
    from pyscriptc.lower import Lowerer  # noqa: F401  # moved-verbatim import


def _excmake_node(exc: str, span: dict[str, Any], message: dict[str, Any] | None, code: int = -1) -> dict[str, Any]:
    from pyscriptc.tirserde import dict_to_span

    return node_to_dict(_ir.excmake(exc, dict_to_span(span), code, dict_to_node(message)))


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


def _is_call_name(node: ast.expr, name: str) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name


def _is_range_call(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "range"


def _raise_node(
    exc: str,
    span: dict[str, Any],
    message: dict[str, Any] | None = None,
    code: int = -1,
    reraise: bool = False,
    excslot: str = "",
    cause: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from pyscriptc.tirserde import dict_to_span

    raise_node = _ir.raise_stmt(exc, dict_to_span(span), code, dict_to_node(message), reraise, excslot)
    if cause is not None:
        raise_node.cause = dict_to_node(cause)
    out = node_to_dict(raise_node)
    if message is None and cause is None:
        # The bare raise dict spells no message key when there is none.
        out.pop("message", None)
    return out


def _substitute_names(node: ast.AST, binding: dict[str, ast.expr]) -> ast.AST:
    """Copy `node` with each `Name` read in `binding` replaced by its expression.

    The raise site evaluates an `__init__`'s super-message with the ctor
    parameters bound to the call arguments; a pure name-for-expression
    copy keeps the spans of the original message (the eval order inside
    the expression is the expression's own).
    """

    if isinstance(node, ast.Name) and node.id in binding and isinstance(node.ctx, ast.Load):
        return copy.deepcopy(binding[node.id])
    for field, value in ast.iter_fields(node):
        if isinstance(value, list):
            replaced = [_substitute_names(item, binding) if isinstance(item, ast.AST) else item for item in value]
            setattr(node, field, replaced)
        elif isinstance(value, ast.AST):
            setattr(node, field, _substitute_names(value, binding))
    return node


def _builtin_call(bname: str, args: list[dict[str, Any]], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The dict-IR builtin call one typed construction spells."""
    return node_to_dict(
        _ir.call(_ir.builtin(bname), _body_nodes(args), dict_to_type(typ), _mk_span_of(span), "", "", -1)
    )


def _is_docstring(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


class StmtBMixin:
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
                _if_stmt(_not_bool(broke, span), else_body, span),
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
        kind = _node_type_kind(iterable)
        if kind == "class" and "__iter__" in self.classes.get(iterable["type"]["name"], {}).get("methods", {}):
            called = self._dunder_call(stmt.iter, iterable, "__iter__", [], span)
            if called is not None:
                iterable = called
                kind = _node_type_kind(iterable)
        if kind == "dict":
            key_t = iterable["type"]["key"]
            if _type_kind(key_t) not in {"int", "str"}:
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
            if _type_kind(elem) not in _SCALAR | {"list", "dict"} and not self._plain_class_elem(elem):
                self.fail(stmt, "PS2002", f"for over list[{_show(elem)}] is not lowered")
        else:
            self.fail(stmt.iter, "PS2002", "for iterates over a list or range")
        unpack = not isinstance(stmt.target, ast.Name)
        target = ""
        fresh = False
        if unpack:
            if _type_kind(elem) != "class" or not self.classes.get(elem["name"], {}).get("tuple"):
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
        # A record class has no zero instance to pre-bind; the loop step binds it before any read.
        record = (
            _type_kind(elem) == "class" and elem["name"] in self.classes and not self.classes[elem["name"]].get("tuple")
        )
        if fresh and not record:
            prefix.append(_assign(target, _zero(elem, span), span))
        prefix.extend(
            [
                _assign(seq, iterable, span),
                _assign(length, _builtin_call("len", [_name(seq, iterable["type"], span)], int_t, span), span),
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
            if _type_kind(elem) != "class" or not self.classes.get(elem["name"], {}).get("tuple"):
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
        advance = node_to_dict(
            _ir.setattr_stmt(
                dict_to_node(itn_n),
                "idx",
                dict_to_node(_binary_int("+", idx_r, _const_int(1, span), span)),
                _mk_span_of(span),
            )
        )
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
        body = self._skip_on_break(self._loop_body(stmt), span)
        self.break_flag = saved_break
        out = [_assign(itn, iterable, span), _assign(flag, _const_bool(False, span), span)]
        if fresh:
            out.append(_assign(target, _zero(elem, span), span))
        out.append(
            node_to_dict(
                _ir.while_stmt(
                    dict_to_node(_not_bool(flag, span)),
                    [
                        dict_to_node(
                            node_to_dict(
                                _ir.try_stmt(
                                    [dict_to_node(_assign(target, step, span))],
                                    [
                                        _ir.Handler(
                                            ["StopIteration"],
                                            [8],
                                            [dict_to_node(_assign(flag, _const_bool(True, span), span))],
                                        )
                                    ],
                                    _mk_span_of(span),
                                )
                            )
                        ),
                        *_body_nodes(body),
                    ],
                    _mk_span_of(span),
                )
            )
        )
        return out

    def _for_range(self, stmt: ast.For, span: dict[str, Any]) -> list[dict[str, Any]]:
        call = stmt.iter
        assert isinstance(call, ast.Call)
        if call.keywords:
            self.fail(call, "PS2003", "keyword arguments are not lowered")
        args = [self._expr(arg, {"kind": "int"}) for arg in call.args]
        for arg in args:
            if _node_type_kind(arg) != "int":
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
                _if_stmt(
                    _compare("==", _name(step_n, int_t, span), _const_int(0, span), span),
                    [_raise_node("ValueError", span)],
                    span,
                ),
                _if_stmt(
                    _compare(">", _name(step_n, int_t, span), _const_int(0, span), span),
                    self._user_while(
                        _compare("<", _name(cur, int_t, span), _name(stop_n, int_t, span), span),
                        up,
                        span,
                        init=False,
                    ),
                    span,
                    self._user_while(
                        _compare(">", _name(cur, int_t, span), _name(stop_n, int_t, span), span),
                        down,
                        span,
                        init=False,
                    ),
                ),
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
                if _node_type_kind(message) != "str":
                    self.fail(stmt.msg, "PS1003", "an exception message is a str")
                then = self.prelude
            finally:
                self.prelude = saved
        then.append(_raise_node("AssertionError", span, message))
        negated = _unary_not(cond, span)
        return [_if_stmt(negated, then, span)]

    def _raise(self, stmt: ast.Raise, span: dict[str, Any]) -> dict[str, Any]:
        # `E(arg)` becomes the message (str(arg)); bool goes through str() so
        # True prints "True". The exc and `raise ... from` calls share one
        # pass; multi-arg ctors fence (repr(tuple) is not cheap).
        #
        # `raise SystemExit(3)` is the exception: an int (or bool, which is an
        # int in CPython) stays typed so the emitter can hand the value to
        # ps_raise_system_exit, and the uncaught program exits with it (#603).
        exit_value = (
            isinstance(stmt.exc, ast.Call) and isinstance(stmt.exc.func, ast.Name) and stmt.exc.func.id == "SystemExit"
        )
        # A user exception class whose __init__ chains the builtin base
        # builds its own message (issue #814): the raw-argument message
        # below would be CPython's message for a class that overrides it.
        chained = self._chained_exc_message(stmt)
        msgs: list[dict[str, Any] | None] = [None, None]
        for which in range(2):
            call = stmt.exc if which == 0 else stmt.cause
            if not isinstance(call, ast.Call):
                continue
            if call.keywords:
                self.fail(call, "PS2003", "keyword arguments are not lowered")
            built: dict[str, Any] | None = None
            if call.args:
                if len(call.args) != 1 and not (which == 0 and chained is not None):
                    self.fail(call, "PS2003", "exception takes one scalar argument")
                if which == 0 and chained is not None:
                    msgs[which] = None
                    continue
                expr = self._expr(call.args[0])
                if exit_value and which == 0 and _node_type_kind(expr) in {"int", "bool"}:
                    cause = stmt.cause
                    if cause is not None and not (isinstance(cause, ast.Constant) and cause.value is None):
                        # A raise-from record carries a str message only, so
                        # there is nowhere to put the status: fence instead of
                        # exiting with the wrong code.
                        self.fail(call, "PS2003", "raise SystemExit with a status and a cause is not lowered")
                    built = expr
                elif _node_type_kind(expr) == "bool":
                    built = _builtin_call("str", [expr], {"kind": "str"}, span)
                elif _node_type_kind(expr) in {"str", "int", "float"}:
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
            node: dict[str, Any] = _raise_node("Exception", span, reraise=True, excslot=slot)
        else:
            exc: ast.expr = stmt.exc
            if isinstance(exc, ast.Call):
                exc = exc.func
            if not isinstance(exc, ast.Name):
                self.fail(stmt, "PS2002", "raise names a lowered exception")
            if exc.id in EXCEPTIONS:
                node = _raise_node(exc.id, span, message)
            elif exc.id in RESERVED_EXCEPTIONS:
                # A reserved class (queue.Empty): the code rides the node.
                node = _raise_node(exc.id, span, message, code=RESERVED_EXCEPTIONS[exc.id])
            else:
                resolved = self.class_aliases.get(exc.id, exc.id)
                info = self.classes.get(resolved)
                if info is None or not info.get("exception"):
                    self.fail(stmt, "PS2002", "raise names a lowered exception")
                # raise makes a code plus optional str; it does not call
                # __init__ (divergence 9). When the class's __init__ chains
                # the builtin base with `super().__init__(msg)`, CPython's
                # message is that expression evaluated over the ctor args:
                # the raise site computes it, so str(e) and the uncaught
                # report read what CPython reads (issue #814).
                if chained is not None:
                    message = chained
                node = _raise_node(resolved, span, message, code=info["exc_code"])
        if stmt.cause is not None:
            cause_node = stmt.cause
            if isinstance(cause_node, ast.Name) and _maybe_type_kind(self.locals.get(cause_node.id, {})) == "exc":
                node["cause"] = node_to_dict(
                    _ir.name_expr(cause_node.id, dict_to_type({"kind": "exc"}), _mk_span_of(span))
                )
            elif not (isinstance(cause_node, ast.Constant) and cause_node.value is None):
                cexc = cause_node
                if isinstance(cause_node, ast.Call):
                    cexc = cause_node.func
                if not isinstance(cexc, ast.Name):
                    self.fail(cause_node, "PS2002", "raise-from needs an exception or None")
                if cexc.id in EXCEPTIONS:
                    node["cause"] = _excmake_node(cexc.id, span, cmessage)
                else:
                    cresolved = self.class_aliases.get(cexc.id, cexc.id)
                    cinfo = self.classes.get(cresolved)
                    if cinfo is None or not cinfo.get("exception"):
                        self.fail(cause_node, "PS2002", "raise-from needs an exception or None")
                    node["cause"] = _excmake_node(cresolved, span, cmessage, code=cinfo["exc_code"])
        return node

    def _chained_exc_message(self, stmt: ast.Raise) -> dict[str, Any] | None:
        """The message a user exception class builds by chaining the builtin
        base, or None when the class does not chain.

        `raise C(args)` on a class whose `__init__` calls
        `super().__init__(msg)` produces CPython's `msg`, not the raw
        argument (issue #814). The chain is walked at the AST level: each
        level substitutes its own parameters with the arguments it was
        called with, so the builtin base's message expression arrives with
        every parameter already bound.
        """

        exc = stmt.exc
        if not isinstance(exc, ast.Call) or not isinstance(exc.func, ast.Name):
            return None
        resolved = self.class_aliases.get(exc.func.id, exc.func.id)
        info = self.classes.get(resolved)
        if info is None or not info.get("exception"):
            return None
        message_node = self._exc_chain_message(resolved, info, list(exc.args))
        if message_node is None:
            return None
        message = self._expr(message_node, {"kind": "str"})
        if _node_type_kind(message) != "str":
            return None
        return message

    def _exc_chain_message(self, resolved: str, info: dict[str, Any], args: list[ast.expr]) -> ast.expr | None:
        """The message expression of an exception class's base chain.

        `args` are the arguments this level was called with, already
        substituted. The level's `__init__` must be one `super().__init__(...)`
        call: the arguments of that call are substituted with this level's
        parameters and handed to the base class, which repeats until the
        builtin base (`Exception` and friends) takes the message.
        """

        class_node = info.get("node")
        if class_node is None:
            return None
        init_node = next(
            (
                stmt
                for stmt in class_node.body
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == "__init__"
            ),
            None,
        )
        if init_node is None:
            return None
        super_calls: list[ast.Call] = []

        def visit(node: ast.AST) -> None:
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "__init__"
                and isinstance(node.func.value, ast.Call)
                and isinstance(node.func.value.func, ast.Name)
                and node.func.value.func.id == "super"
            ):
                super_calls.append(node)
                return
            for child in ast.iter_child_nodes(node):
                visit(child)

        for stmt in init_node.body:
            if _is_docstring(stmt):
                continue
            visit(stmt.value if isinstance(stmt, ast.Expr) else stmt)
        if len(super_calls) != 1:
            return None
        super_call = super_calls[0]
        params = [param["name"] for param in info["methods"].get("__init__", ([], None))[0] if param["name"] != "self"]
        if len(params) != len(args):
            return None
        # This level's parameters bind to the arguments it was called with;
        # the super call's own arguments are the next level's arguments.
        binding = dict(zip(params, args, strict=True))
        substituted = [_substitute_names(copy.deepcopy(arg), binding) for arg in super_call.args]
        target = self._super_init_target(resolved, info)
        if target is not None:
            base_name, base = target
            return self._exc_chain_message(base_name, base, substituted)
        # The builtin base: `Exception.__init__(message)` takes the message.
        if len(substituted) != 1:
            return None
        return substituted[0]

    def _super_init_target(self, resolved: str, info: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        """The user exception class one MRO step up, or None when the chain
        lands on the builtin base (`Exception` and friends)."""

        for name in info.get("mro", [resolved])[1:]:
            if name in EXCEPTIONS:
                return None
            candidate = self.classes.get(name)
            if candidate is not None and candidate.get("exception") and "__init__" in candidate.get("methods", {}):
                return name, candidate
        return None

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
                elif _maybe_type_kind(prior) != "exc":
                    self.fail(handler, "PS1205", f"{bind} is {_show(prior)}, and except-as binds an exception")
                self.dead_names.discard(bind)
            self.exc_stack.append(bind or "")
            try:
                body = self._body(list(handler.body))
            finally:
                slot = self.exc_stack.pop()
                if bind is not None:
                    self.dead_names.add(bind)  # CPython emits `del e` at handler end

            flags = _ir.F_BARE if handler.type is None else 0
            # The try statement's own handlers always spell "codes" (empty
            # when the exception carries none); the with-family handlers
            # below omit the key entirely.
            handlers.append(
                _ir.Handler(
                    names,
                    codes,
                    [dict_to_node(item) for item in body],
                    slot or (bind or ""),
                    bind or "",
                    flags,
                )
            )
        body = self._body(list(stmt.body))
        if stmt.orelse:
            orelse = self._body(list(stmt.orelse))
            brk_s = brk or ""
            cont_s = cont or ""
        else:
            orelse = None
            brk_s = ""
            cont_s = ""
        if stmt.finalbody:
            finalbody = self._body(list(stmt.finalbody))
        else:
            finalbody = None
        node = node_to_dict(
            _ir.try_stmt(
                _body_nodes(body),
                handlers,
                _mk_span_of(span),
                None if orelse is None else [dict_to_node(item) for item in orelse],
                None if finalbody is None else [dict_to_node(item) for item in finalbody],
                brk_s,
                cont_s,
            )
        )
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
                node_to_dict(
                    _ir.try_stmt(
                        _body_nodes(self._body(list(stmt.body))),
                        [_ir.Handler([ctx.args[0].id], None, [])],
                        _mk_span_of(span),
                    )
                )
            ]
        if self._is_mod(ctx, "contextlib", "nullcontext"):
            if item.optional_vars is not None:
                self.fail(stmt, "PS1206", "with-as is not lowered")
            assert isinstance(ctx, ast.Call)
            if ctx.args:
                self.fail(ctx, "PS2003", "nullcontext takes no arguments")
            return self._body(list(stmt.body))
        ctx_v = self._expr(ctx)
        if _node_type_kind(ctx_v) == "class" and ctx_v["type"].get("name") in {"TextIO", "BytesIO"}:
            return self._with_io(stmt, item, ctx_v, span)
        if _node_type_kind(ctx_v) == "class" and "__enter__" in self.classes.get(ctx_v["type"].get("name"), {}).get(
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
        if _node_type_kind(count) != "int":
            self.fail(call, "PS1003", "itertools.repeat count is int")
        if _node_type_kind(value) not in _SCALAR:
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
        if any(_node_type_kind(item) != "list" for item in lists):
            self.fail(call, "PS2002", "itertools.chain takes lists")
        elem = lists[0]["type"]["elem"]
        if _type_kind(elem) not in _SCALAR:
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
            _assign(length, _builtin_call("len", [_name(seq, iterable["type"], span)], int_t, span), span),
            _assign(idx, _const_int(0, span), span),
            *self._user_while(
                _compare("<", _name(idx, int_t, span), _name(length, int_t, span), span),
                [
                    _assign(
                        target,
                        node_to_dict(
                            _ir.subscript(
                                dict_to_node(_name(seq, iterable["type"], span)),
                                dict_to_node(_name(idx, int_t, span)),
                                dict_to_type(elem),
                                _mk_span_of(span),
                            )
                        ),
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
            if _type_kind(key_t) not in {"int", "str"}:
                self.fail(key_node, "PS2002", "dict keys are int or str")
            if _type_kind(elem) == "bool":
                self.fail(self.comp_elt, "PS2002", "dict values of bool are not lowered")
            typ = {"kind": "dict", "key": key_t, "value": elem}
            what = "dict"
        elif self.comp_kind == "set":
            if _type_kind(elem) not in {"int", "str"}:
                self.fail(self.comp_elt, "PS2002", "set elements are int or str")
            typ = {"kind": "list", "elem": elem, "set": True}
            what = "set"
        else:
            if self.comp_pred is None and (
                _type_kind(elem) == "bool"
                or _type_kind(elem)
                not in {
                    "int",
                    "float",
                    "str",
                    "bytes",
                    "list",
                    "dict",
                    "class",
                }
            ):
                self.fail(self.comp_elt, "PS2002", f"list[{_show(elem)}] is not lowered")
            typ = {"kind": "list", "elem": elem}
            what = "list"
        if self.comp_expect is not None and _type_kind(self.comp_expect) == _type_kind(typ):
            self._expect(self.comp_elt, typ, self.comp_expect, what)
        if not self.comp_ready:
            span = span_of(self.comp_elt, self.filename)
            if self.comp_pred is not None:
                self.comp_init = _assign(self.comp_pred[0], _const_bool(self.comp_pred[1], span), span)
            else:
                self.locals[self.comp_dest] = typ
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
        if self.comp_pred is not None:
            # The `all`/`any` sink: the element's truth updates the bool
            # accumulator in place of a list push, so a bool element never
            # materializes a `list[bool]`.
            pred_name = self.comp_pred[0]
            want_all = self.comp_pred[1]
            truth = self._truthy(elt, elt["span"])
            if want_all:
                upd = _if_stmt(
                    _unary_not(truth, elt["span"]),
                    [_assign(pred_name, _const_bool(False, elt["span"]), elt["span"])],
                    elt["span"],
                )
            else:
                upd = _if_stmt(
                    truth,
                    [_assign(pred_name, _const_bool(True, elt["span"]), elt["span"])],
                    elt["span"],
                )
            kept = [*inner, upd]
        elif self.comp_kind == "dict":
            kslot = self._temp("key")
            vslot = self._temp("elt")
            self.locals[kslot] = key["type"]
            self.locals[vslot] = elem
            kept = [
                *inner,
                _assign(kslot, key, key["span"]),
                _assign(vslot, elt, elt["span"]),
                node_to_dict(
                    _ir.setitem_stmt(
                        dict_to_node(_name(self.comp_dest, typ, elt["span"])),
                        dict_to_node(_name(kslot, key["type"], elt["span"])),
                        dict_to_node(_name(vslot, elem, elt["span"])),
                        _mk_span_of(elt["span"]),
                    )
                ),
            ]
        elif self.comp_kind == "set":
            kept = [*inner, *self._set_insert_stmts(self.comp_dest, typ, elt, elt["span"])]
        else:
            slot = self._temp("elt")
            self.locals[slot] = elem
            push = node_to_dict(
                _ir.expr_stmt(
                    _ir.call(
                        _ir.module_callee("builtins", "list.append"),
                        _body_nodes([_name(self.comp_dest, typ, elt["span"]), _name(slot, elem, elt["span"])]),
                        dict_to_type({"kind": "none"}),
                        _mk_span_of(elt["span"]),
                        "ps_list_push",
                        "",
                        -1,
                    ),
                    _mk_span_of(elt["span"]),
                )
            )
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
                _if_stmt(filt, kept, span_of(cond, self.filename)),
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
        if _node_type_kind(opened) != "class" or opened["type"].get("name") != "Path":
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
            made = node_to_dict(
                _ir.call(_ir.class_callee(typ["name"]), [], dict_to_type(typ), _mk_span_of(span), "", "", -1)
            )
            stmts = [_assign(temp, made, span)]
            if value is not None:
                stmts += [
                    node_to_dict(
                        _ir.setattr_stmt(
                            dict_to_node(_name(temp, typ, span)),
                            "_tag",
                            dict_to_node(_const_bool(True, span)),
                            _mk_span_of(span),
                        )
                    ),
                    node_to_dict(
                        _ir.setattr_stmt(
                            dict_to_node(_name(temp, typ, span)), "_val", dict_to_node(value), _mk_span_of(span)
                        )
                    ),
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
            out.append(_expr_wrap(enter, span))
        else:
            self.locals[target.id] = enter["type"]
            out.append(_assign(target.id, enter, span))
        body = self._body(list(stmt.body))
        handler_body = [
            *et_stmts,
            *ev_stmts,
            *tb_stmts,
            _assign(suppressed, raised_exit, span),
            _if_stmt(
                _not_bool(suppressed, span),
                [_raise_node("Exception", span, reraise=True, excslot=exc_slot)],
                span,
            ),
        ]
        out.append(
            node_to_dict(
                _ir.try_stmt(
                    [
                        *dict_to_list(et0_stmts),
                        *dict_to_list(ev0_stmts),
                        *dict_to_list(tb0_stmts),
                        *dict_to_list(body),
                        dict_to_node(_expr_wrap(clean_exit, span)),
                    ],
                    [
                        _ir.Handler(
                            ["Exception"], None, [dict_to_node(item) for item in handler_body], exc_slot, exc_slot
                        )
                    ],
                    _mk_span_of(span),
                )
            )
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
        close = _expr_wrap(
            self._abi_call("io", "close", [_name(target, opened["type"], span)], {"kind": "none"}, "ps_io_close", span),
            span,
        )
        body = self._inject_close(self._body(list(stmt.body)), close)
        return [
            _assign(target, opened, span),
            node_to_dict(
                _ir.try_stmt(
                    [*dict_to_list(body), dict_to_node(close)],
                    [
                        _ir.Handler(
                            ["Exception"],
                            None,
                            [dict_to_node(close), dict_to_node(_raise_node("Exception", span, reraise=True))],
                        )
                    ],
                    _mk_span_of(span),
                )
            ),
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
        close = _expr_wrap(self._abi_call("io", "close", [hold_n], {"kind": "none"}, "ps_io_close", span), span)
        body = self._inject_close(self._body(list(stmt.body)), close)
        out.append(
            node_to_dict(
                _ir.try_stmt(
                    [*dict_to_list(body), dict_to_node(close)],
                    [
                        _ir.Handler(
                            ["Exception"],
                            None,
                            [dict_to_node(close), dict_to_node(_raise_node("Exception", span, reraise=True))],
                        )
                    ],
                    _mk_span_of(span),
                )
            )
        )
        return out

    def _for_enumerate(self, stmt: ast.For, span: dict[str, Any]) -> list[dict[str, Any]]:
        call = stmt.iter
        assert isinstance(call, ast.Call)
        start_node = None
        for kw in call.keywords:
            if kw.arg is None:
                self.fail(call, "PS2002", kwargs_call_note())
            if kw.arg == "start":
                if start_node is not None:
                    self.fail(call, "PS2003", "enumerate start given more than once")
                start_node = kw.value
            else:
                self.fail(call, "PS2003", f"enumerate keyword {kw.arg} is not lowered")
        if len(call.args) not in {1, 2}:
            self.fail(call, "PS2003", "enumerate takes 1 or 2 arguments")
        if len(call.args) == 2:
            if start_node is not None:
                self.fail(call, "PS2003", "enumerate start given more than once")
            start_node = call.args[1]
        source = self._expr(call.args[0])
        elem = self._pair_source_elem(call, source, "enumerate")
        start = _const_int(0, span)
        if start_node is not None:
            start = self._expr(start_node, {"kind": "int"})
            if _node_type_kind(start) != "int":
                self.fail(call, "PS1003", "enumerate start is int")
        return self._for_pairs(stmt, span, [source], [elem], start, "enumerate")

    def _for_zip(self, stmt: ast.For, span: dict[str, Any]) -> list[dict[str, Any]]:
        call = stmt.iter
        assert isinstance(call, ast.Call)
        if call.keywords or len(call.args) < 2:
            self.fail(call, "PS2003", "zip takes at least 2 lists")
        sources = [self._expr(arg) for arg in call.args]
        elems: list[dict[str, Any]] = []
        for source in sources:
            elems.append(self._pair_source_elem(call, source, "zip"))
        return self._for_pairs(stmt, span, sources, elems, None, "zip")

    def _pair_source_elem(self, call: ast.Call, source: dict[str, Any], what: str) -> dict[str, Any]:
        """The element type one enumerate/zip source iterates, or a fence.

        A source is a non-set `list` of scalars or a `str` (which iterates
        its one-character strs); anything else keeps the PS2002 fence.
        """
        kind = _node_type_kind(source)
        if kind == "str":
            return {"kind": "str"}
        if kind != "list" or source["type"].get("set"):
            self.fail(call, "PS2002", f"{what}() takes lists")
        elem = source["type"]["elem"]
        if _type_kind(elem) not in _SCALAR | {"bool"}:
            self.fail(call, "PS2002", f"{what}() of list[{_show(elem)}] is not lowered")
        return elem

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
                node_to_dict(
                    _ir.subscript(
                        dict_to_node(_name(seq, source["type"], span)),
                        dict_to_node(_name(idx, int_t, span)),
                        dict_to_type(elem),
                        _mk_span_of(span),
                    )
                )
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
        # zip stops at the shortest source; enumerate has one. Each extra
        # source folds its length into `length` through its own temp.
        prefix.append(_assign(length, _len_call(_name(seqs[0], sources[0]["type"], span), span), span))
        for seq, source in zip(seqs[1:], sources[1:], strict=True):
            n_tmp = self._temp("n")
            self.locals[n_tmp] = int_t
            prefix.append(_assign(n_tmp, _len_call(_name(seq, source["type"], span), span), span))
            prefix.append(
                _if_stmt(
                    _compare("<", _name(n_tmp, int_t, span), _name(length, int_t, span), span),
                    [_assign(length, _name(n_tmp, int_t, span), span)],
                    span,
                )
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
            if _node_kind(stmt) == "return":
                value = stmt["value"]
                if _node_kind(value) == "const" and _node_type_kind(value) == "none":
                    out.append(close)
                    out.append(stmt)
                    continue
                temp = self._temp("withret")
                self.locals[temp] = value["type"]
                out.append(_assign(temp, value, value["span"]))
                out.append(close)
                out.append({**stmt, "value": _name(temp, value["type"], value["span"])})
                continue
            if _node_kind(stmt) == "if":
                out.append(
                    {
                        **stmt,
                        "then": self._inject_close(stmt["then"], close),
                        "else": self._inject_close(stmt["else"], close),
                    }
                )
                continue
            if _node_kind(stmt) == "while":
                out.append({**stmt, "body": self._inject_close(stmt["body"], close)})
                continue
            if _node_kind(stmt) == "try":
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
