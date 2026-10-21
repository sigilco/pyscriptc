"""itertools call lowering."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._iterators import _elem_list, _fn_ref, _lambda_params, _mk_iter
from pyscriptc.restlower._shared import (
    _asg,
    _cmp,
    _emit,
    _field,
    _incr,
    _int,
    _len_of,
    _nm,
    _pin,
    _same,
    _set_field,
    _span_to,
    _tname,
)


def _pool(lo: Any, node: ast.AST, source: dict[str, Any], span: dict[str, Any], hint: str) -> dict[str, Any]:
    """Materialize an iterable argument into a named `list[T]`: iterator
    records drain to the end like `list(it)`, and other kinds go through the
    `for` lowering (str, bytes, dict keys, files)."""
    typ = source["type"]
    name = _tname(lo, hint)
    if typ["kind"] == "class" and lo.classes.get(typ["name"], {}).get("iterator"):
        list_t = {"kind": "list", "elem": typ["elem"]}
        itn = _pin(lo, source, span, hint)
        seq_r = _field(itn, "seq", list_t, span)
        lo.locals[name] = list_t
        lo.prelude.extend(
            [
                _asg(
                    name,
                    lo._abi_call(
                        "builtins",
                        "list",
                        [
                            seq_r,
                            _field(itn, "idx", {"kind": "int"}, span),
                            _int(0, span),
                            _int(1, span),
                            _int(0, span),
                            _int(1, span),
                        ],
                        list_t,
                        "ps_seq_slice",
                        span,
                    ),
                    span,
                ),
                _set_field(itn, "idx", _len_of(seq_r, span), span),
            ]
        )
        return _nm(name, list_t, span)
    seq = _elem_list(lo, node, source, span, hint) if typ["kind"] != "list" else source
    lo.locals[name] = seq["type"]
    lo.prelude.append(_asg(name, seq, span))
    return _nm(name, seq["type"], span)


def _fn_call(
    lo: Any, fn: ast.expr, args: list[str], typs: list[dict[str, Any]], span: dict[str, Any], who: str
) -> tuple[list[str], str, list[str], dict[str, dict[str, Any] | None]]:
    """`(pre, text, post, saves)` — generated lines for `fn(*args)` in the
    sorted-block style: an inline lambda binds its own parameter names first,
    colliding locals are saved and restored, and `text` is the application."""
    if not isinstance(fn, ast.Lambda):
        ref = _fn_ref(lo, fn, span, who)
        return [], f"{ast.unparse(ref)}({', '.join(args)})", [], {}
    names = _lambda_params(lo, fn, len(args), who)
    pre, post, saves = [], [], {}
    for name, typ in zip(names, typs, strict=True):
        saved = lo.locals.get(name)
        saves[name] = saved
        if saved is not None:
            keep = _tname(lo, "fkeep")
            lo.locals[keep] = saved
            pre.append(f"{keep} = {name}")
            post.append(f"{name} = {keep}")
        lo.locals[name] = typ
    pre += [f"{name} = {arg}" for name, arg in zip(names, args, strict=True)]
    return pre, ast.unparse(fn.body), post, saves


def _fn_restore(lo: Any, saves: dict[str, dict[str, Any] | None]) -> None:
    # Fresh parameter names stay bound like a `for` target does, so their
    # generated assigns get a slot in the function's locals; only a
    # pre-existing local's declared type is restored here (its value is
    # restored by the generated `post` lines).
    for name, saved in saves.items():
        if saved is not None:
            lo.locals[name] = saved


def _islice(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`islice(xs, a, b, s)` is the `xs[a:b:s]` slice of the materialized
    elements; on an iterator record it consumes exactly `stop` from the
    cursor, like CPython's bounded pull."""
    lo._reject_keywords(node)
    if len(node.args) not in {2, 3, 4}:
        lo.fail(node, "PS2003", "itertools.islice expects 2 to 4 argument(s)")
    bounds = [lo._expr(arg) for arg in node.args[1:]]
    for arg in bounds:
        if arg["type"]["kind"] != "int":
            lo.fail(node, "PS1003", "itertools.islice bounds are int")
    start = _int(0, span) if len(bounds) == 1 else bounds[0]
    stop = bounds[0] if len(bounds) == 1 else bounds[1]
    step = bounds[2] if len(bounds) == 3 else _int(1, span)
    # CPython raises ValueError on negative bounds or a non-positive step.
    for val, floor in ((start, 0), (stop, 0), (step, 1)):
        lo.prelude.append(
            {
                "kind": "if",
                "cond": _cmp("<", val, _int(floor, span), span),
                "then": [{"kind": "raise", "exc": "ValueError", "message": None, "span": span}],
                "else": [],
                "span": span,
            }
        )
    source = lo._expr(node.args[0])
    typ = source["type"]
    if typ["kind"] == "class" and lo.classes.get(typ["name"], {}).get("iterator"):
        itn = _pin(lo, source, span, "sl")
        elem = typ["elem"]
        seq_r = _field(itn, "seq", {"kind": "list", "elem": elem}, span)
        idx_r = _field(itn, "idx", {"kind": "int"}, span)
        new_idx = {
            "kind": "binary",
            "op": "+",
            "left": idx_r,
            "right": stop,
            "type": {"kind": "int"},
            "span": span,
        }
        sliced = lo._abi_call(
            "builtins",
            "list",
            [
                seq_r,
                {
                    "kind": "binary",
                    "op": "+",
                    "left": _field(itn, "idx", {"kind": "int"}, span),
                    "right": start,
                    "type": {"kind": "int"},
                    "span": span,
                },
                new_idx,
                _int(1, span),
                _int(1, span),
                step,
            ],
            {"kind": "list", "elem": elem},
            "ps_seq_slice",
            span,
        )
        sliced_pin = _pin(lo, sliced, span, "sls")
        lo.prelude.append(
            {
                "kind": "if",
                "cond": _cmp("<=", new_idx, _len_of(seq_r, span), span),
                "then": [_set_field(itn, "idx", new_idx, span)],
                "else": [_set_field(itn, "idx", _len_of(seq_r, span), span)],
                "span": span,
            }
        )
        return _mk_iter(lo, sliced_pin, node, span)
    seq = _pool(lo, node, source, span, "sl")
    sliced = lo._abi_call(
        "builtins", "list", [seq, start, stop, _int(1, span), _int(1, span), step], seq["type"], "ps_seq_slice", span
    )
    return _mk_iter(lo, sliced, node, span)


def _product(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`product(a, b, ...)` is the nested-comprehension cross product over
    materialized pools; `repeat=k` repeats the same pools, like CPython."""
    repeat = 1
    for kw in node.keywords:
        if kw.arg == "repeat":
            if not isinstance(kw.value, ast.Constant) or not isinstance(kw.value.value, int) or kw.value.value < 1:
                lo.fail(kw, "PS2003", "itertools.product repeat= is a literal positive int")
            repeat = kw.value.value
        else:
            lo._reject_keywords(node)
    pools = [_pool(lo, node, lo._expr(arg), span, "pl") for arg in node.args]
    pools *= repeat
    if len(pools) < 2:
        lo.fail(node, "PS2003", "itertools.product takes at least two iterables")
    names = [lo._temp("pv") for _ in pools]
    comp = ast.ListComp(
        elt=ast.Tuple(elts=[ast.Name(id=n, ctx=ast.Load()) for n in names], ctx=ast.Load()),
        generators=[
            ast.comprehension(
                target=ast.Name(id=n, ctx=ast.Store()),
                iter=ast.Name(id=p["id"], ctx=ast.Load()),
                ifs=[],
                is_async=False,
            )
            for n, p in zip(names, pools, strict=True)
        ],
    )
    return _mk_iter(lo, lo._comp(_span_to(node, comp), span, None), node, span)


def _combinations(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`combinations(xs, r)` is the nested-comprehension over index ranges
    `i0 < i1 < ...` of the materialized pool."""
    lo._reject_keywords(node)
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "itertools.combinations takes a pool and a count")
    count = node.args[1]
    if not isinstance(count, ast.Constant) or not isinstance(count.value, int) or count.value < 2:
        lo.fail(count, "PS2003", "itertools.combinations count is a literal int of 2 or more")
    r = count.value
    pool = _pool(lo, node, lo._expr(node.args[0]), span, "cb")
    n = _tname(lo, "cbn")
    lo.locals[n] = {"kind": "int"}
    lo.prelude.append(_asg(n, _len_of(pool, span), span))
    idxs = [lo._temp("ci") for _ in range(r)]
    gens = []
    for k in range(r):
        args = [ast.Name(id=n, ctx=ast.Load())]
        if k:
            args.insert(
                0, ast.BinOp(left=ast.Name(id=idxs[k - 1], ctx=ast.Load()), op=ast.Add(), right=ast.Constant(value=1))
            )
        gens.append(
            ast.comprehension(
                target=ast.Name(id=idxs[k], ctx=ast.Store()),
                iter=ast.Call(func=ast.Name(id="range", ctx=ast.Load()), args=args, keywords=[]),
                ifs=[],
                is_async=False,
            )
        )
    comp = ast.ListComp(
        elt=ast.Tuple(
            elts=[
                ast.Subscript(
                    value=ast.Name(id=pool["id"], ctx=ast.Load()), slice=ast.Name(id=i, ctx=ast.Load()), ctx=ast.Load()
                )
                for i in idxs
            ],
            ctx=ast.Load(),
        ),
        generators=gens,
    )
    return _mk_iter(lo, lo._comp(_span_to(node, comp), span, None), node, span)


def _accumulate(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`accumulate(xs[, f])` yields running folds: the first element as-is,
    then `acc = f(acc, x)` (or `acc + x` with no function)."""
    lo._reject_keywords(node)
    if len(node.args) not in {1, 2}:
        lo.fail(node, "PS2003", "itertools.accumulate takes an iterable and an optional function")
    pool = _pool(lo, node, lo._expr(node.args[0]), span, "ac")
    elem = pool["type"]["elem"]
    out, acc, i, n = (_tname(lo, h) for h in ("aco", "acc", "aci", "acn"))
    lo.locals[out] = {"kind": "list", "elem": elem}
    lo.locals[acc] = elem
    lo.locals[i] = {"kind": "int"}
    lo.locals[n] = {"kind": "int"}
    if len(node.args) == 2:
        pre, step, post, saves = _fn_call(
            lo, node.args[1], [acc, f"{pool['id']}[{i}]"], [elem, elem], span, "accumulate"
        )
        step_lines = [*pre, f"{acc} = {step}"]
    else:
        post, saves = [], {}
        step_lines = [f"{acc} = {acc} + {pool['id']}[{i}]"]
    _emit(
        lo,
        node,
        [
            f"{out} = []",
            f"{n} = len({pool['id']})",
            f"{i} = 0",
            f"while {i} < {n}:",
            f"    if {i} == 0:",
            f"        {acc} = {pool['id']}[{i}]",
            "    else:",
            *[f"        {line}" for line in step_lines],
            f"    {out}.append({acc})",
            f"    {i} = {i} + 1",
            *post,
        ],
        span,
    )
    _fn_restore(lo, saves)
    return _mk_iter(lo, _nm(out, lo.locals[out], span), node, span)


def _ziplongest(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`zip_longest(a, b, ...)` pads the shorter pools: `fillvalue=None`
    makes every field the tagged `T | None`; an explicit fillvalue must
    match every element type and the fields stay unwrapped."""
    fill = None
    for kw in node.keywords:
        if kw.arg == "fillvalue":
            fill = kw.value
        else:
            lo._reject_keywords(node)
    if len(node.args) < 2:
        lo.fail(node, "PS2003", "itertools.zip_longest takes at least two iterables")
    pools = [_pool(lo, node, lo._expr(arg), span, "zl") for arg in node.args]
    elems = [p["type"]["elem"] for p in pools]
    fill_expr = lo._expr(fill) if fill is not None else None
    if fill_expr is None or fill_expr["type"]["kind"] == "none":
        fields = [lo._opt_type(e, node) for e in elems]
        fill_src = "None"
    else:
        for e in elems:
            if not _same(fill_expr["type"], e):
                lo.fail(node, "PS1003", "itertools.zip_longest fillvalue matches the element types")
        fields = elems
        fill_src = _tname(lo, "zlf")
        lo.locals[fill_src] = fill_expr["type"]
        lo.prelude.append(_asg(fill_src, fill_expr, span))
    tup_t = lo._tuple_type(fields, node)
    out, i, n = (_tname(lo, h) for h in ("zlo", "zli", "zln"))
    lo.locals[out] = {"kind": "list", "elem": tup_t}
    lo.locals[i] = {"kind": "int"}
    lo.locals[n] = {"kind": "int"}
    lens, vals, lines = [], [], []
    for k, p in enumerate(pools):
        ln, va = _tname(lo, f"zk{k}"), _tname(lo, f"zv{k}")
        lo.locals[ln] = {"kind": "int"}
        lo.locals[va] = fields[k]
        lines.append(f"{ln} = len({p['id']})")
        lens.append(ln)
        vals.append(va)
    lines.append(f"{n} = {lens[0]}")
    for ln in lens[1:]:
        lines.append(f"if {ln} > {n}: {n} = {ln}")
    lines += [f"{out} = []", f"{i} = 0", f"while {i} < {n}:"]
    for k, p in enumerate(pools):
        lines += [
            f"    if {i} < {lens[k]}:",
            f"        {vals[k]} = {p['id']}[{i}]",
            "    else:",
            f"        {vals[k]} = {fill_src}",
        ]
    lines += [f"    {out}.append(({', '.join(vals)}))", f"    {i} = {i} + 1"]
    _emit(lo, node, lines, span)
    return _mk_iter(lo, _nm(out, lo.locals[out], span), node, span)


def _takewhile(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`takewhile(p, xs)` keeps the leading run while `p(x)` holds; an
    iterator source's cursor consumes through the first failure, like
    CPython's lazy pull."""
    lo._reject_keywords(node)
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "itertools.takewhile takes a predicate and an iterable")
    source = lo._expr(node.args[1])
    typ = source["type"]
    int_t = {"kind": "int"}
    itn = None
    if typ["kind"] == "class" and lo.classes.get(typ["name"], {}).get("iterator"):
        itn = _pin(lo, source, span, "tw")
        elem = typ["elem"]
        list_t = {"kind": "list", "elem": elem}
        seq_r = _field(itn, "seq", list_t, span)
        pool = _tname(lo, "twp")
        lo.locals[pool] = list_t
        lo.prelude.append(
            _asg(
                pool,
                lo._abi_call(
                    "builtins",
                    "list",
                    [
                        seq_r,
                        _field(itn, "idx", int_t, span),
                        _int(0, span),
                        _int(1, span),
                        _int(0, span),
                        _int(1, span),
                    ],
                    list_t,
                    "ps_seq_slice",
                    span,
                ),
                span,
            )
        )
    else:
        pool_nm = _pool(lo, node, source, span, "twp")
        pool = pool_nm["id"]
        elem = pool_nm["type"]["elem"]
    out, i, n, v = (_tname(lo, h) for h in ("two", "twi", "twn", "twv"))
    lo.locals[out] = {"kind": "list", "elem": elem}
    lo.locals[i] = int_t
    lo.locals[n] = int_t
    lo.locals[v] = elem
    pre, test, post, saves = _fn_call(lo, node.args[0], [v], [elem], span, "takewhile")
    _emit(
        lo,
        node,
        [
            f"{out} = []",
            f"{n} = len({pool})",
            f"{i} = 0",
            f"while {i} < {n}:",
            f"    {v} = {pool}[{i}]",
            *[f"    {line}" for line in pre],
            f"    if {test}:",
            f"        {out}.append({v})",
            f"        {i} = {i} + 1",
            "    else:",
            "        break",
            *post,
        ],
        span,
    )
    _fn_restore(lo, saves)
    if itn is not None:
        lo.prelude.extend(
            [
                _set_field(
                    itn,
                    "idx",
                    {
                        "kind": "binary",
                        "op": "+",
                        "left": _field(itn, "idx", int_t, span),
                        "right": _nm(i, int_t, span),
                        "type": int_t,
                        "span": span,
                    },
                    span,
                ),
                {
                    "kind": "if",
                    "cond": _cmp("<", _nm(i, int_t, span), _nm(n, int_t, span), span),
                    "then": [_set_field(itn, "idx", _incr(_field(itn, "idx", int_t, span), 1, span), span)],
                    "else": [],
                    "span": span,
                },
            ]
        )
    return _mk_iter(lo, _nm(out, lo.locals[out], span), node, span)


def _groupby(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`groupby(xs[, key])` yields `(key, group)` pairs where the group is
    the materialized run of equal keys — a list standing in for CPython's
    grouper iterator."""
    lo._reject_keywords(node)
    if len(node.args) not in {1, 2}:
        lo.fail(node, "PS2003", "itertools.groupby takes an iterable and an optional key function")
    pool = _pool(lo, node, lo._expr(node.args[0]), span, "gb")
    elem = pool["type"]["elem"]
    v = _tname(lo, "gbv")
    if len(node.args) == 2:
        key = node.args[1]
        if isinstance(key, ast.Lambda):
            names = _lambda_params(lo, key, 1, "groupby")
            saved = lo.locals.get(names[0])
            lo.locals[names[0]] = elem
            pre_len = len(lo.prelude)
            probe = lo._expr(key.body)
            del lo.prelude[pre_len:]
            if saved is None:
                del lo.locals[names[0]]
            else:
                lo.locals[names[0]] = saved
            key_t = probe["type"]
        else:
            ftype = lo._expr(key)["type"]
            if ftype["kind"] != "func":
                lo.fail(key, "PS2003", "itertools.groupby key is a function name or an inline lambda")
            key_t = ftype["return"]
        pre, ktext, post, saves = _fn_call(lo, key, [v], [elem], span, "groupby")
    else:
        key_t = elem
        pre, post, saves = [], [], {}
        ktext = v
    list_t = {"kind": "list", "elem": elem}
    tup_t = lo._tuple_type([key_t, list_t], node)
    g, out, i, n, k = (_tname(lo, h) for h in ("gbg", "gbo", "gbi", "gbn", "gbk"))
    lo.locals[out] = {"kind": "list", "elem": tup_t}
    lo.locals[g] = list_t
    lo.locals[k] = key_t
    lo.locals[v] = elem
    lo.locals[i] = {"kind": "int"}
    lo.locals[n] = {"kind": "int"}
    _emit(
        lo,
        node,
        [
            f"{out} = []",
            f"{n} = len({pool['id']})",
            f"{i} = 0",
            f"while {i} < {n}:",
            f"    {v} = {pool['id']}[{i}]",
            *[f"    {line}" for line in pre],
            f"    {k} = {ktext}",
            f"    {g} = []",
            f"    while {i} < {n}:",
            f"        {v} = {pool['id']}[{i}]",
            *[f"        {line}" for line in pre],
            f"        if ({ktext}) == {k}:",
            f"            {g}.append({v})",
            f"            {i} = {i} + 1",
            "        else:",
            "            break",
            f"    {out}.append(({k}, {g}))",
            *post,
        ],
        span,
    )
    _fn_restore(lo, saves)
    return _mk_iter(lo, _nm(out, lo.locals[out], span), node, span)


def _itertools(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """`itertools` calls that materialize eagerly under the R24 iterator
    model: each returns a cursor record over a computed list, so `for`,
    `next`, and `list` drain it like `map`/`zip`. The infinite `count` and
    `cycle` stay fenced — the eager model has no lazy pull to bound them;
    `repeat(x, n)` covers the bounded spelling."""
    if attr == "islice":
        return _islice(lo, node, span)
    if attr == "product":
        return _product(lo, node, span)
    if attr == "combinations":
        return _combinations(lo, node, span)
    if attr == "accumulate":
        return _accumulate(lo, node, span)
    if attr == "zip_longest":
        return _ziplongest(lo, node, span)
    if attr == "takewhile":
        return _takewhile(lo, node, span)
    if attr == "groupby":
        return _groupby(lo, node, span)
    lo.fail(node, "PS2001", f"itertools.{attr} is not in the lowered subset of itertools")
    raise AssertionError("unreachable")
