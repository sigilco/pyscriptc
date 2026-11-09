"""functools.reduce lowering."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import (
    _asg,
    _binary,
    _cmp,
    _flag,
    _if_stmt,
    _int,
    _len_of,
    _nm,
    _node_type,
    _node_type_kind,
    _raise_stmt,
    _same,
    _sub,
    _t_leaf,
    _type_kind,
    _while_stmt,
)


def _reduce(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`functools.reduce(operator.add, xs)` is a loop of the existing int `+`.

    A direct function name stays the existing call. `operator.add` has no
    function value, so that spelling stores the list once and adds each later
    element. An initializer on `operator.add` stays fenced. An empty list
    raises ValueError; this subset has no TypeError.
    """
    lo._reject_keywords(node)
    if len(node.args) not in {2, 3}:
        lo.fail(node, "PS2003", "functools.reduce expects 2 or 3 argument(s)")
    fn = node.args[0]
    if _is_operator_add(lo, fn):
        if len(node.args) == 3:
            lo.fail(node, "PS2002", "functools.reduce initializer is not lowered")
        return _reduce_add(lo, node, span)
    if not isinstance(fn, ast.Name) or fn.id not in lo.sigs:
        lo.fail(node, "PS2001", "functools.reduce takes a direct function name")
    params, ret = lo.sigs[fn.id]
    if (
        len(params) != 2
        or not _same(_node_type(params[0]), _node_type(params[1]))
        or not _same(_node_type(params[0]), ret)
    ):
        lo.fail(node, "PS2001", "functools.reduce function is (T, T) -> T")
    seq = lo._expr(node.args[1])
    if _node_type_kind(seq) != "list" or not _same(_node_type(seq)["elem"], ret):
        lo.fail(node, "PS1003", "functools.reduce takes a list of the function's type")
    args = [seq]
    if len(node.args) == 3:
        init = lo._expr(node.args[2], ret)
        lo._expect(node, _node_type(init), ret, "functools.reduce initializer")
        args.append(init)
    expr = lo._abi_call("functools", "reduce", args, ret, "", span)
    expr.pop("symbol", None)
    expr["fn"] = fn.id
    return expr


def _reduce_add(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    seq = lo._expr(node.args[1])
    seq_t = _node_type(seq)
    if _type_kind(seq_t) != "list" or _flag(seq_t, "set") or _type_kind(seq_t["elem"]) != "int":
        lo.fail(node, "PS1003", "functools.reduce takes a list of int")
    int_t = _t_leaf("int")
    list_t = seq_t
    src = lo._temp("reds")
    acc = lo._temp("reda")
    idx = lo._temp("redi")
    length = lo._temp("redn")
    lo.locals[src] = list_t
    lo.locals[acc] = int_t
    lo.locals[idx] = int_t
    lo.locals[length] = int_t
    src_n = _nm(src, list_t, span)
    acc_n = _nm(acc, int_t, span)
    idx_n = _nm(idx, int_t, span)
    length_n = _nm(length, int_t, span)
    item = _sub(src_n, idx_n, int_t, span)
    lo.prelude.extend(
        [
            _asg(src, seq, span),
            _asg(length, _len_of(src_n, span), span),
            _if_stmt(
                _cmp("==", length_n, _int(0, span), span),
                [_raise_stmt("ValueError", None, span)],
                [],
                span,
            ),
            _asg(acc, _sub(src_n, _int(0, span), int_t, span), span),
            _asg(idx, _int(1, span), span),
            _while_stmt(
                _cmp("<", idx_n, length_n, span),
                [
                    _asg(acc, _binary("+", acc_n, item, int_t, span), span),
                    _asg(idx, _binary("+", idx_n, _int(1, span), int_t, span), span),
                ],
                span,
            ),
        ]
    )
    return _nm(acc, int_t, span)


def _is_operator_add(lo: Any, node: ast.expr) -> bool:
    if isinstance(node, ast.Attribute):
        resolved = lo._resolve_module(node)
        return resolved is not None and resolved[1] == "operator" and resolved[2] == "add"
    if isinstance(node, ast.Name):
        binding = lo.bindings.get(node.id)
        return binding is not None and binding.module == "operator" and binding.attr == "add"
    return False
