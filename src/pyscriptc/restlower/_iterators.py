"""Iterator builtins and the helpers that build lazy iterator state."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import _asg, _cmp, _field, _incr, _int, _len_of, _nm, _pin, _set_field, _span_to, _sub


def _elem_list(lo: Any, node: ast.Call, source: dict[str, Any], span: dict[str, Any], hint: str) -> dict[str, Any]:
    """Materialize a non-list iterable into a list through the same `for`
    lowering a comprehension uses (str, bytes, files, dict keys elsewhere);
    non-iterables fence inside `_for` with the usual message."""

    held = _pin(lo, source, span, hint)
    var = lo._temp(hint)
    comp = ast.ListComp(
        elt=ast.Name(id=var, ctx=ast.Load()),
        generators=[
            ast.comprehension(
                target=ast.Name(id=var, ctx=ast.Store()),
                iter=ast.Name(id=held["id"], ctx=ast.Load()),
                ifs=[],
                is_async=False,
            )
        ],
    )
    return lo._comp(_span_to(node, comp), span, None)


def _mk_iter(lo: Any, seq: dict[str, Any], node: ast.AST, span: dict[str, Any]) -> dict[str, Any]:
    """Wrap a materialized `list[T]` in an iterator record {seq, idx}: the
    cursor `next()`/`for`/`list()` advance. R24's eager model — the elements
    are computed up front; the record only tracks position."""

    it_t = lo._iter_type(seq["type"]["elem"], node)
    itn = lo._temp("it")
    lo.locals[itn] = it_t
    made = {
        "kind": "call",
        "callee": {"kind": "class", "name": it_t["name"]},
        "args": [],
        "type": it_t,
        "span": span,
    }
    lo.prelude.extend(
        [
            _asg(itn, made, span),
            _set_field(_nm(itn, it_t, span), "seq", seq, span),
            _set_field(_nm(itn, it_t, span), "idx", _int(0, span), span),
        ]
    )
    return _nm(itn, it_t, span)


def _iter(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`it = iter(xs)`: an iterator record {seq, idx} over the source. Eager
    model: dict/str/bytes/file sources materialize first, lists are shared,
    and `iter(it)` is the same cursor."""

    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "iter expects 1 argument(s)")
    source = lo._expr(node.args[0])
    src_t = source["type"]
    if src_t["kind"] == "class" and lo.classes.get(src_t["name"], {}).get("iterator"):
        return source
    if src_t["kind"] == "class":
        called = lo._dunder_call(node, source, "__iter__", [], span)
        if called is not None:
            return called
    if src_t["kind"] == "list":
        seq = source
    elif src_t["kind"] == "dict":
        key_t = src_t["key"]
        if key_t["kind"] not in {"int", "str"}:
            lo.fail(node.args[0], "PS2002", "dict keys are int or str")
        seq = lo._abi_call("builtins", "dict.keys", [source], {"kind": "list", "elem": key_t}, "ps_dict_keys", span)
    else:
        seq = _elem_list(lo, node, source, span, "ivs")
    return _mk_iter(lo, seq, node, span)


def _next(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`next(it[, d])`: reads seq[idx] and advances the cursor; raises
    StopIteration once spent, or yields `d` for the 2-arg form."""

    lo._reject_keywords(node)
    if not 1 <= len(node.args) <= 2:
        lo.fail(node, "PS2003", "next takes an iterator and an optional default")
    source = lo._expr(node.args[0])
    src_t = source["type"]
    if src_t["kind"] == "class" and not lo.classes.get(src_t["name"], {}).get("iterator"):
        # A user class with __next__ is an iterator itself; the 2-arg form
        # catches its StopIteration for the default.
        called = lo._dunder_call(node, source, "__next__", [], span)
        if called is not None:
            if len(node.args) == 1:
                return called
            elem = called["type"]
            default = lo._expr(node.args[1])
            lo._expect(node.args[1], default["type"], elem, "next")
            out = lo._temp("nx")
            lo.locals[out] = elem
            lo.prelude.append(
                {
                    "kind": "try",
                    "body": [_asg(out, called, span)],
                    "handlers": [{"exc": ["StopIteration"], "codes": [8], "body": [_asg(out, default, span)]}],
                    "span": span,
                }
            )
            return _nm(out, elem, span)
    if src_t["kind"] != "class" or not lo.classes.get(src_t["name"], {}).get("iterator"):
        lo.fail(node.args[0], "PS2002", "next takes an iterator value")
    elem = src_t["elem"]
    itn = _pin(lo, source, span, "it")
    int_t = {"kind": "int"}
    seq_t = {"kind": "list", "elem": elem}
    seq_r = _field(itn, "seq", seq_t, span)
    idx_r = _field(itn, "idx", int_t, span)
    item = _sub(seq_r, idx_r, elem, span)
    advance = _set_field(itn, "idx", _incr(idx_r, 1, span), span)
    out = lo._temp("nx")
    lo.locals[out] = elem
    if len(node.args) == 1:
        lo.prelude.extend(
            [
                {
                    "kind": "if",
                    "cond": _cmp(">=", idx_r, _len_of(seq_r, span), span),
                    "then": [{"kind": "raise", "exc": "StopIteration", "message": None, "span": span}],
                    "else": [],
                    "span": span,
                },
                _asg(out, item, span),
                advance,
            ]
        )
        return _nm(out, elem, span)
    default = lo._expr(node.args[1])
    lo._expect(node.args[1], default["type"], elem, "next")
    lo.prelude.append(
        {
            "kind": "if",
            "cond": _cmp("<", idx_r, _len_of(seq_r, span), span),
            "then": [_asg(out, item, span), advance],
            "else": [_asg(out, default, span)],
            "span": span,
        }
    )
    return _nm(out, elem, span)


def _fn_ref(lo: Any, fn: ast.expr, span: dict[str, Any], who: str) -> ast.expr:
    """map/filter's callable argument: names and attributes call directly;
    any other expression lowering to a func value is pinned so the
    synthesized element call can name it."""

    if isinstance(fn, (ast.Name, ast.Attribute)):
        return fn
    value = lo._expr(fn)
    if value["type"]["kind"] != "func":
        lo.fail(fn, "PS2003", f"{who} takes a function name or an inline lambda")
    temp = lo._temp("fn")
    lo.locals[temp] = value["type"]
    lo.prelude.append(_asg(temp, value, span))
    return ast.Name(id=temp, ctx=ast.Load())


def _lambda_params(lo: Any, lam: ast.Lambda, want: int, who: str) -> list[str]:
    args = lam.args
    plain = args.posonlyargs + args.args
    if args.vararg or args.kwarg or args.kwonlyargs or args.defaults or any(args.kw_defaults or []):
        lo.fail(lam, "PS2003", f"a {who} lambda takes plain positional parameters")
    if len(plain) != want:
        lo.fail(lam, "PS2003", f"a {who} lambda takes {want} parameter(s)")
    return [arg.arg for arg in plain]


def _map(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`map(f, xs...)` lowers to the comprehension `[f(x, ...) for x, ... in
    xs|zip(xs...)]` behind an iterator record — the elements materialize
    eagerly, and the result drains once, like CPython's `map`."""

    lo._reject_keywords(node)
    if len(node.args) < 2:
        lo.fail(node, "PS2003", "map takes a function and at least one iterable")
    fn = node.args[0]
    iters = list(node.args[1:])
    if isinstance(fn, ast.Lambda):
        names = _lambda_params(lo, fn, len(iters), "map")
        elt = fn.body
    else:
        names = [lo._temp("mv") for _ in iters]
        func = _fn_ref(lo, fn, span, "map")
        elt = ast.Call(func=func, args=[ast.Name(id=n, ctx=ast.Load()) for n in names], keywords=[])
    target = (
        ast.Name(id=names[0], ctx=ast.Store())
        if len(names) == 1
        else ast.Tuple(elts=[ast.Name(id=n, ctx=ast.Store()) for n in names], ctx=ast.Store())
    )
    iter_node = (
        iters[0] if len(iters) == 1 else ast.Call(func=ast.Name(id="zip", ctx=ast.Load()), args=iters, keywords=[])
    )
    comp = ast.ListComp(
        elt=elt,
        generators=[ast.comprehension(target=target, iter=iter_node, ifs=[], is_async=False)],
    )
    seq = lo._comp(_span_to(node, comp), span, None)
    return _mk_iter(lo, seq, node, span)


def _filter(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`filter(f, xs)` lowers to `[x for x in xs if f(x)]` behind an iterator
    record; `filter(None, xs)` keeps the truthy elements."""

    lo._reject_keywords(node)
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "filter takes a function and an iterable")
    fn, xs = node.args
    if isinstance(fn, ast.Lambda):
        names = _lambda_params(lo, fn, 1, "filter")
        var = names[0]
        ifs = [fn.body]
    elif isinstance(fn, ast.Constant) and fn.value is None:
        var = lo._temp("fx")
        ifs = [ast.Name(id=var, ctx=ast.Load())]
    else:
        var = lo._temp("fx")
        func = _fn_ref(lo, fn, span, "filter")
        ifs = [ast.Call(func=func, args=[ast.Name(id=var, ctx=ast.Load())], keywords=[])]
    comp = ast.ListComp(
        elt=ast.Name(id=var, ctx=ast.Load()),
        generators=[ast.comprehension(target=ast.Name(id=var, ctx=ast.Store()), iter=xs, ifs=ifs, is_async=False)],
    )
    seq = lo._comp(_span_to(node, comp), span, None)
    return _mk_iter(lo, seq, node, span)


def _reversed(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`reversed(xs)` is an iterator over the step--1 sequence slice of the
    materialized elements (dict reverses the key list)."""

    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "reversed expects 1 argument(s)")
    source = lo._expr(node.args[0])
    src_t = source["type"]
    if src_t["kind"] == "list" and not src_t.get("set"):
        seq = source
    elif src_t["kind"] == "dict":
        key_t = src_t["key"]
        if key_t["kind"] not in {"int", "str"}:
            lo.fail(node.args[0], "PS2002", "dict keys are int or str")
        seq = lo._abi_call("builtins", "dict.keys", [source], {"kind": "list", "elem": key_t}, "ps_dict_keys", span)
    elif src_t["kind"] in {"str", "bytes"}:
        seq = _elem_list(lo, node, source, span, "rvs")
    else:
        lo.fail(node.args[0], "PS2002", "reversed takes a sequence")
        raise AssertionError("unreachable")
    elem = seq["type"]["elem"]
    if elem["kind"] == "bool":
        lo.fail(node, "PS2002", "list[bool] is not lowered")
    rev = lo._abi_call(
        "builtins",
        "reversed",
        [seq, _int(0, span), _int(0, span), _int(0, span), _int(0, span), _int(-1, span)],
        {"kind": "list", "elem": elem},
        "ps_seq_slice",
        span,
    )
    return _mk_iter(lo, rev, node, span)


def _range_value(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`r = range(n)` as a value: the materialized list of the same items the
    `for i in range(n)` form yields."""

    lo._reject_keywords(node)
    var = lo._temp("rv")
    comp = ast.ListComp(
        elt=ast.Name(id=var, ctx=ast.Load()),
        generators=[ast.comprehension(target=ast.Name(id=var, ctx=ast.Store()), iter=node, ifs=[], is_async=False)],
    )
    return lo._comp(_span_to(node, comp), span, None)


def _enumerate_value(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`e = enumerate(xs)` as a value: an iterator record over the
    materialized (index, item) pairs the for-loop form yields."""

    lo._reject_keywords(node)
    index = lo._temp("ei")
    var = lo._temp("ev")
    comp = ast.ListComp(
        elt=ast.Tuple(
            elts=[ast.Name(id=index, ctx=ast.Load()), ast.Name(id=var, ctx=ast.Load())],
            ctx=ast.Load(),
        ),
        generators=[
            ast.comprehension(
                target=ast.Tuple(
                    elts=[ast.Name(id=index, ctx=ast.Store()), ast.Name(id=var, ctx=ast.Store())],
                    ctx=ast.Store(),
                ),
                iter=node,
                ifs=[],
                is_async=False,
            )
        ],
    )
    seq = lo._comp(_span_to(node, comp), span, None)
    return _mk_iter(lo, seq, node, span)


def _zip_value(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`z = zip(a, b)` as a value: an iterator record over the materialized
    pairs the for-loop form yields."""

    lo._reject_keywords(node)
    names = [lo._temp("zv") for _ in node.args]
    comp = ast.ListComp(
        elt=ast.Tuple(elts=[ast.Name(id=n, ctx=ast.Load()) for n in names], ctx=ast.Load()),
        generators=[
            ast.comprehension(
                target=ast.Tuple(elts=[ast.Name(id=n, ctx=ast.Store()) for n in names], ctx=ast.Store()),
                iter=node,
                ifs=[],
                is_async=False,
            )
        ],
    )
    seq = lo._comp(_span_to(node, comp), span, None)
    return _mk_iter(lo, seq, node, span)
