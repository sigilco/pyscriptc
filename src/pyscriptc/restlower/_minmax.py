"""min, max, sum and sorted lowering."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.reflection import kwargs_call_note
from pyscriptc.restlower._shared import (
    _asg,
    _call_builtin,
    _cmp,
    _const_text,
    _expr_stmt,
    _if_stmt,
    _int,
    _len_of,
    _list_lit,
    _list_node,
    _nm,
    _node_elems,
    _node_id,
    _node_kind,
    _node_type,
    _node_type_kind,
    _node_value,
    _pin,
    _raise_stmt,
    _span_to,
    _sub,
    _t_leaf,
    _t_list,
    _type_kind,
    _type_name,
)


def _minmax(lo: Any, node: ast.Call, span: dict[str, Any], name: str) -> dict[str, Any]:
    """min/max: two scalars stay a builtin call; the iterable form folds into sorted().

    A stable sort picks the first of equal keys, which is CPython's min/max
    tie-break; max is `sorted(reverse=True)[0]`. An empty sequence raises
    ValueError before the sort, matching CPython's message.
    """
    key = None
    for kw in node.keywords:
        if kw.arg is None:
            lo.fail(node, "PS2002", kwargs_call_note())
        if kw.arg == "key":
            key = kw.value
        else:
            lo._reject_keywords(node)
    if key is None and len(node.args) == 2:
        left = lo._expr(node.args[0])
        right = lo._expr(node.args[1])
        left_kind = _node_type_kind(left)
        if left_kind != _node_type_kind(right) or left_kind not in _SORT_ELEM:
            lo.fail(node, "PS2003", f"{name} takes two values of the same scalar type")
        return _call_builtin(name, [left, right], _node_type(left), span)
    if not node.args:
        lo.fail(node, "PS2003", f"{name} expects at least 1 argument(s)")
    if len(node.args) == 1:
        arg = lo._expr(node.args[0])
    else:
        elems = [lo._expr(item) for item in node.args]
        kind = _node_type_kind(elems[0])
        if kind not in _SORT_ELEM or any(_node_type_kind(elem) != kind for elem in elems):
            lo.fail(node, "PS2003", f"{name} takes scalars of one type")
        arg = _list_lit(elems, _node_type(elems[0]), span)
    arg_type = _node_type(arg)
    if _type_kind(arg_type) == "dict":
        seq = _pin(lo, arg, span, "mmseq")
        keys_call = ast.Call(
            func=ast.Attribute(value=ast.Name(id=_node_id(seq), ctx=ast.Load()), attr="keys", ctx=ast.Load()),
            args=[],
            keywords=[],
        )
        arg = lo._expr(_span_to(node, keys_call))
        arg_type = _node_type(arg)
    if _type_kind(arg_type) != "list" or _type_kind(arg_type["elem"]) not in _SORT_ELEM:
        lo.fail(node, "PS2003", f"{name} takes a list of int, float, str, or bytes")
    if key is None:
        kind = _type_kind(arg_type["elem"])
        symbol = "ps_" + name + "_" + {"int": "i64", "float": "f64", "str": "buf", "bytes": "buf"}[kind]
        return lo._abi_call("builtins", name, [arg], _t_leaf(kind), symbol, span)
    seq = _pin(lo, arg, span, "mmseq")
    len_call = _len_of(seq, span)
    empty = _cmp("==", len_call, _int(0, span), span)
    lo.prelude.append(
        _if_stmt(
            empty,
            [
                _raise_stmt(
                    "ValueError",
                    _const_text(f"{name}() iterable argument is empty", "str", span),
                    span,
                )
            ],
            [],
            span,
        )
    )
    kws = [ast.keyword(arg="key", value=key)]
    if name == "max":
        kws.append(ast.keyword(arg="reverse", value=ast.Constant(value=True)))
    call = ast.Call(
        func=ast.Name(id="sorted", ctx=ast.Load()), args=[ast.Name(id=_node_id(seq), ctx=ast.Load())], keywords=kws
    )
    ordered = _sorted(lo, _span_to(node, call), span)
    return _sub(ordered, _int(0, span), arg_type["elem"], span)


def _sum(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """sum(xs) is a runtime fold; sum(xs, start) pins the fold and adds once.

    The `+` rides the normal binary lowering, so int + float promotes like
    CPython. `start` is keyword-or-positional like CPython.
    """
    start = None
    for kw in node.keywords:
        if kw.arg is None:
            lo.fail(node, "PS2002", kwargs_call_note())
        if kw.arg == "start":
            start = kw.value
        else:
            lo._reject_keywords(node)
    if len(node.args) == 2:
        if start is not None:
            lo.fail(node, "PS2003", "sum got multiple values for argument 'start'")
        start = node.args[1]
    elif len(node.args) != 1:
        lo.fail(node, "PS2003", "sum expects 1 or 2 argument(s)")
    arg = lo._expr(node.args[0])
    src = _node_type(arg)
    cls = lo.classes.get(_type_name(src), {}) if _type_kind(src) == "class" else {}
    if cls.get("iterator") or cls.get("coro") == "generator":
        # sum over an iterator or generator: drain the remaining elements
        # through a comprehension (the eager model materializes them into
        # a list; the cursor or frame ends spent, like list(it)).
        pinned = _pin(lo, arg, span, "sum")
        var = lo._temp("v").replace(".", "_")
        arg = lo._expr(
            _span_to(
                node,
                ast.GeneratorExp(
                    elt=ast.Name(id=var, ctx=ast.Load()),
                    generators=[
                        ast.comprehension(
                            target=ast.Name(id=var, ctx=ast.Store()),
                            iter=ast.Name(id=_node_id(pinned), ctx=ast.Load()),
                            ifs=[],
                            is_async=False,
                        )
                    ],
                ),
            )
        )
    arg_type = _node_type(arg)
    if _type_kind(arg_type) != "list" or _type_kind(arg_type["elem"]) not in {"int", "float"}:
        lo.fail(node, "PS2003", "sum takes a list of int or float")
    kind = _type_kind(arg_type["elem"])
    symbol = "ps_sum_i64" if kind == "int" else "ps_sum_f64"
    call = lo._abi_call("builtins", "sum", [arg], _t_leaf(kind), symbol, span)
    if start is None:
        return call
    total = _pin(lo, call, span, "sum")
    add = ast.BinOp(left=ast.Name(id=_node_id(total), ctx=ast.Load()), op=ast.Add(), right=start)
    return lo._expr(_span_to(node, add))


_SORT_ELEM = {"int", "float", "str", "bytes"}


def _sorted(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "sorted expects 1 argument(s)")
    reverse = False
    key = None
    for kw in node.keywords:
        if kw.arg is None:
            lo.fail(node, "PS2002", kwargs_call_note())
        if kw.arg == "key":
            key = kw.value
        elif kw.arg == "reverse":
            if not isinstance(kw.value, ast.Constant) or not isinstance(kw.value.value, bool):
                lo.fail(kw, "PS2003", "sorted reverse= takes a True or False literal")
            reverse = kw.value.value
        else:
            lo._reject_keywords(node)
    if key is not None:
        lam = key
        if not isinstance(lam, ast.Lambda):
            lo.fail(lam, "PS2003", "sorted key= takes an inline lambda")
        args = lam.args
        params = args.posonlyargs + args.args
        variadic = args.vararg or args.kwarg or args.kwonlyargs or args.defaults or any(args.kw_defaults)
        if len(params) != 1 or variadic:
            lo.fail(lam, "PS2003", "a sorted key= lambda takes one positional parameter")
        for sub in ast.walk(lam.body):
            if isinstance(sub, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                lo.fail(lam, "PS2003", "a sorted key= lambda is a plain expression")

        arg = lo._expr(node.args[0])
        kind = _node_type_kind(arg)
        arg_type = _node_type(arg)
        if kind == "dict":
            elem = arg_type["key"]
            elems_expr = "{s}.keys()"
        elif kind == "list":
            elem = arg_type["elem"]
            elems_expr = "{s}"
        else:
            lo.fail(node.args[0], "PS2003", "sorted key= takes a list or dict")
            raise AssertionError("unreachable")
        if _type_kind(elem) not in _SORT_ELEM:
            lo.fail(node.args[0], "PS2003", "sorted key= iterates int, float, str, or bytes elements")

        w = params[0].arg
        fields = lam.body.elts if isinstance(lam.body, ast.Tuple) else [lam.body]

        saved_w = lo.locals.get(w)
        lo.locals[w] = elem
        probes = [lo._expr(field) for field in fields]
        if saved_w is None:
            del lo.locals[w]
        else:
            lo.locals[w] = saved_w
        if any(_node_type_kind(p) not in _SORT_ELEM for p in probes):
            lo.fail(lam, "PS2003", "a sorted key= lambda returns scalars or a tuple of scalars")

        def temp(hint: str) -> str:
            return lo._temp(hint).replace(".", "_")

        s = temp("seq")
        e = temp("elems")
        keys = [temp(f"k{i}") for i in range(len(fields))]
        n = temp("n")
        idx = temp("idx")
        i = temp("i")
        j = temp("j")
        v = temp("v")
        out = temp("out")

        lo.locals[s] = arg_type
        lo.prelude.append(_asg(s, arg, span))
        lo.locals[e] = _t_list(elem)
        for p, k in zip(probes, keys, strict=True):
            lo.locals[k] = _t_list(_node_type(p))
        for t in (n, i, j, v):
            lo.locals[t] = _t_leaf("int")
        lo.locals[idx] = _t_list(_t_leaf("int"))
        lo.locals[out] = _t_list(elem)

        # reverse=True flips the comparator; the insertion sort stays
        # stable, so equal keys keep their original order like CPython.
        cmp_op = ">" if reverse else "<"

        def less(field: int) -> str:
            jb = f"{idx}[{j} - 1]"
            lt = f"{keys[field]}[{v}] {cmp_op} {keys[field]}[{jb}]"
            if field == len(keys) - 1:
                return lt
            return f"{lt} or ({keys[field]}[{v}] == {keys[field]}[{jb}] and ({less(field + 1)}))"

        # The block reuses the lambda's own parameter name as the loop
        # variable; a pre-existing local is pinned and restored after.
        lines = [f"{e} = {elems_expr.format(s=s)}"]
        if saved_w is not None:
            save_w = temp("save")
            lo.locals[save_w] = elem
            lines.append(f"{save_w} = {w}")
        for k in keys:
            lines.append(f"{k} = []")
        lines.append(f"for {w} in {e}:")
        for pos, k in enumerate(keys):
            lines.append(f"    {k}.append({ast.unparse(fields[pos])})")
        if saved_w is not None:
            lines.append(f"{w} = {save_w}")
        lines += [
            f"{n} = len({keys[0]})",
            f"{idx} = []",
            f"{i} = 0",
            f"while {i} < {n}:",
            f"    {idx}.append({i})",
            f"    {i} = {i} + 1",
            f"{i} = 1",
            f"while {i} < {n}:",
            f"    {v} = {idx}[{i}]",
            f"    {j} = {i}",
            f"    while {j} > 0 and ({less(0)}):",
            f"        {idx}[{j}] = {idx}[{j} - 1]",
            f"        {j} = {j} - 1",
            f"    {idx}[{j}] = {v}",
            f"    {i} = {i} + 1",
            f"{out} = []",
            f"{i} = 0",
            f"while {i} < {n}:",
            f"    {out}.append({e}[{idx}[{i}]])",
            f"    {i} = {i} + 1",
        ]
        for stmt in ast.parse("\n".join(lines)).body:
            for sub in ast.walk(stmt):
                sub.lineno, sub.col_offset = node.lineno, node.col_offset
                sub.end_lineno, sub.end_col_offset = node.end_lineno, node.end_col_offset
            lo.prelude.extend(lo._body([stmt]))
        return _nm(out, _t_list(elem), span)
    arg = lo._expr(node.args[0])
    arg_type = _node_type(arg)
    if _type_kind(arg_type) != "list" or _type_kind(arg_type["elem"]) not in _SORT_ELEM:
        lo.fail(node, "PS2003", "sorted takes a list of int, float, str, or bytes")
    folded = _const_sorted(arg, span)
    if folded is not None:
        if reverse:
            folded = {**folded, "elems": _node_elems(folded)[::-1]}
        return folded
    kind = _type_kind(arg_type["elem"])
    symbol = {"int": "ps_sorted_i64", "float": "ps_sorted_f64", "str": "ps_sorted_buf", "bytes": "ps_sorted_buf"}[kind]
    call = lo._abi_call("builtins", "sorted", [arg], arg_type, symbol, span)
    if not reverse:
        return call
    seq = _pin(lo, call, span, "sort")
    lo.prelude.append(
        _expr_stmt(
            lo._abi_call("builtins", "list.reverse", [seq], _t_leaf("none"), "ps_seq_reverse", span),
            span,
        )
    )
    return seq


def _const_sorted(arg: dict[str, Any], span: dict[str, Any]) -> dict[str, Any] | None:
    """sorted() of a constant list is that list. Int constants sort as integers, not their digits."""

    if _node_kind(arg) != "list":
        return None
    elems = _node_elems(arg)
    if any(_node_kind(item) != "const" for item in elems):
        return None
    kind = _type_kind(_node_type(arg)["elem"])
    ordered: list[dict[str, Any]] = []
    if kind == "int":
        ordered = sorted(elems, key=lambda item: int(_node_value(item)))
    elif kind in {"str", "bytes"}:
        ordered = sorted(elems, key=lambda item: _node_value(item))
    else:
        return None
    return _list_node(ordered, _node_type(arg), span)
