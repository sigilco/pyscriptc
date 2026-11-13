"""statistics call lowering: a closed subset over `list[int]` and `list[float]`.

The subset (issue #486): mean, fmean, median, median_low, median_high,
mode, variance, pvariance, stdev, pstdev, quantiles (exclusive, n=4).

`list[int]` data is exact: the variance family folds the deviations into
integers, the mean family divides the integer sum with `/` (lowered as a
float divide, so the value is exact) and the median family indexes the
sorted copy. One type seam: CPython's `statistics` is polymorphic in its
result (`mean([1, 2, 3])` returns the int 2, the variance family returns
an int when the exact fraction is integral); the IR has one static return
type, so the mean and variance families return float. The value matches;
the repr differs when CPython would have said int. Recorded as D55.

`list[float]` data folds in double arithmetic; CPython converts every
float to an exact rational (`as_integer_ratio`) and converts the result
to float at the end, so a minority of datasets round differently. Also
recorded in D55.

`mode` counts with a `dict[int, int]`, so it takes `list[int]` only;
float data fences (PS1003). `quantiles` implements the default exclusive
method for `n=4`, `n=2`, and `n=8`.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import (
    _asg,
    _emit,
    _nm,
    _node_type,
    _node_type_kind,
    _t_leaf,
    _t_list,
    _tname,
)

_FUNCTIONS = frozenset(
    {
        "mean",
        "fmean",
        "median",
        "median_low",
        "median_high",
        "mode",
        "variance",
        "pvariance",
        "stdev",
        "pstdev",
        "quantiles",
    }
)

_SORTED_HINT = {
    "mean": "sm",
    "fmean": "fm",
    "median": "md",
    "median_low": "ml",
    "median_high": "mh",
    "variance": "vr",
    "pvariance": "pv",
    "stdev": "sd",
    "pstdev": "ps",
    "quantiles": "qt",
}


def _statistics_call(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    if attr not in _FUNCTIONS:
        lo.fail(node, "PS2001", f"statistics.{attr} is not in the lowered subset of statistics")
    if attr == "quantiles":
        _quantiles_keywords(lo, node)
    else:
        lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", f"statistics.{attr} expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    typ = _node_type(arg)
    if _node_type_kind(arg) != "list":
        lo.fail(node, "PS1003", f"statistics.{attr} takes a list")
    elem = typ["elem"]
    if elem["kind"] not in {"int", "float"}:
        lo.fail(node, "PS1003", f"statistics.{attr} takes a list of int or float")
    if attr == "mode" and elem["kind"] != "int":
        lo.fail(node, "PS1003", "statistics.mode takes a list of int (float data is not in the subset)")
    if attr == "quantiles":
        return _quantiles(lo, node, span, arg, elem["kind"])
    handler = {
        "mean": _meanish,
        "fmean": _meanish,
        "median": _median,
        "median_low": _median,
        "median_high": _median,
        "mode": _mode,
        "variance": _varianceish,
        "pvariance": _varianceish,
        "stdev": _stdevish,
        "pstdev": _stdevish,
    }[attr]
    return handler(lo, node, span, arg, attr, elem["kind"])


def _quantiles_keywords(lo: Any, node: ast.Call) -> None:
    for kw in node.keywords:
        if kw.arg != "n":
            lo._reject_keywords(node)


def _copy_into(lo: Any, node: ast.Call, span: dict[str, Any], name: str, arg: dict[str, Any], kind: str) -> None:
    """`name = list(arg)` through the normal call path: a real copy, not an
    alias, so later mutation of `name` leaves the caller's list alone. The
    argument is pinned to a temp first because a const literal has no `id`."""

    src = _tname(lo, "src")
    lo.locals[src] = _t_list(_t_leaf(kind))
    lo.prelude.append(_asg(src, arg, span))
    _emit(lo, node, [f"{name} = list({src})"], span)


def _sorted_copy(
    lo: Any, node: ast.Call, span: dict[str, Any], arg: dict[str, Any], kind: str, hint: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pin the data into a fresh local copy and insertion-sort it; the
    caller's list is left unmodified like every CPython statistics function."""

    name = _tname(lo, hint)
    lo.locals[name] = _t_list(_t_leaf(kind))
    _copy_into(lo, node, span, name, arg, kind)
    seq = _nm(name, _t_list(_t_leaf(kind)), span)
    n = _tname(lo, "n")
    i = _tname(lo, "i")
    j = _tname(lo, "j")
    v = _tname(lo, "v")
    for t in (n, i, j):
        lo.locals[t] = _t_leaf("int")
    lo.locals[v] = _t_leaf(kind)
    _emit(
        lo,
        node,
        [
            f"{n} = len({name})",
            f"{i} = 1",
            f"while {i} < {n}:",
            f"    {v} = {seq['id']}[{i}]",
            f"    {j} = {i}",
            f"    while {j} > 0 and {seq['id']}[{j} - 1] > {v}:",
            f"        {seq['id']}[{j}] = {seq['id']}[{j} - 1]",
            f"        {j} = {j} - 1",
            f"    {seq['id']}[{j}] = {v}",
            f"    {i} = {i} + 1",
        ],
        span,
    )
    return seq, _nm(n, _t_leaf("int"), span)


def _meanish(
    lo: Any, node: ast.Call, span: dict[str, Any], arg: dict[str, Any], attr: str, kind: str
) -> dict[str, Any]:
    """sum / n as a float divide: exact for int data, double for float."""

    seq, n = _sorted_copy(lo, node, span, arg, kind, _SORTED_HINT[attr])
    total = _tname(lo, "t")
    lo.locals[total] = _t_leaf("float")
    i = _tname(lo, "i")
    lo.locals[i] = _t_leaf("int")
    result = _tname(lo, "r")
    lo.locals[result] = _t_leaf("float")
    _emit(
        lo,
        node,
        [
            f"{total} = 0.0",
            f"{i} = 0",
            f"while {i} < {n['id']}:",
            f"    {total} = {total} + {seq['id']}[{i}]",
            f"    {i} = {i} + 1",
            f"{result} = {total} / {n['id']}",
        ],
        span,
    )
    return _nm(result, _t_leaf("float"), span)


def _median(lo: Any, node: ast.Call, span: dict[str, Any], arg: dict[str, Any], attr: str, kind: str) -> dict[str, Any]:
    seq, n = _sorted_copy(lo, node, span, arg, kind, _SORTED_HINT[attr])
    half = _tname(lo, "h")
    lo.locals[half] = _t_leaf("int")
    result = _tname(lo, "r")
    # `median` over int data is typed float: the even-length branch is a
    # float divide, and one static return type covers both branches. The
    # odd-length repr seam (3 vs 3.0) is part of D55; median_low/high keep
    # the element type, matching CPython exactly.
    lo.locals[result] = _t_leaf("float" if attr == "median" else kind)
    if attr == "median_low":
        lines = [f"{result} = {seq['id']}[({n['id']} - 1) // 2]"]
    elif attr == "median_high":
        lines = [f"{result} = {seq['id']}[{n['id']} // 2]"]
    else:
        lines = [
            f"{half} = {n['id']} // 2",
            f"if {n['id']} % 2 == 1:",
            f"    {result} = {seq['id']}[{half}] * 1.0",
            "else:",
            f"    {result} = ({seq['id']}[{half} - 1] + {seq['id']}[{half}]) / 2",
        ]
    _emit(lo, node, lines, span)
    return _nm(result, lo.locals[result], span)


def _mode(lo: Any, node: ast.Call, span: dict[str, Any], arg: dict[str, Any], attr: str, kind: str) -> dict[str, Any]:
    name = _tname(lo, "md")
    lo.locals[name] = _t_list(_t_leaf(kind))
    _copy_into(lo, node, span, name, arg, kind)
    seq = _nm(name, _t_list(_t_leaf(kind)), span)
    counts = _tname(lo, "c")
    lo.locals[counts] = {"kind": "dict", "key": _t_leaf("int"), "value": _t_leaf("int")}
    best = _tname(lo, "b")
    seen = _tname(lo, "s")
    lo.locals[best] = _t_leaf("int")
    lo.locals[seen] = _t_leaf("int")
    x = _tname(lo, "x")
    lo.locals[x] = _t_leaf("int")
    k = _tname(lo, "k")
    lo.locals[k] = _t_leaf("int")
    _emit(
        lo,
        node,
        [
            f"{counts} = {{}}",
            f"for {x} in {seq['id']}:",
            f"    if {x} in {counts}:",
            f"        {counts}[{x}] = {counts}[{x}] + 1",
            "    else:",
            f"        {counts}[{x}] = 1",
            # Second pass over the insertion-ordered dict: strict > keeps
            # the first key that reached the top count, CPython's tie rule.
            f"{best} = 0",
            f"{seen} = -1",
            f"for {k} in {counts}:",
            f"    if {counts}[{k}] > {seen}:",
            f"        {seen} = {counts}[{k}]",
            f"        {best} = {k}",
        ],
        span,
    )
    return _nm(best, _t_leaf("int"), span)


def _varianceish(
    lo: Any, node: ast.Call, span: dict[str, Any], arg: dict[str, Any], attr: str, kind: str
) -> dict[str, Any]:
    """Exact two-pass fold over integer deviations: sum(x)^2 stays in the
    integer domain, so int data is bit-exact against the CPython Fraction
    path (the float conversion happens once, at the end)."""

    if kind == "float":
        lo.fail(node, "PS2001", f"statistics.{attr} over float data is not in the lowered subset (D55)")
    seq, n = _sorted_copy(lo, node, span, arg, kind, _SORTED_HINT[attr])
    total = _tname(lo, "t")
    sst = _tname(lo, "q")
    num = _tname(lo, "j")
    den = _tname(lo, "m")
    i = _tname(lo, "i")
    x = _tname(lo, "x")
    for t in (total, sst, num, den, i, x):
        lo.locals[t] = _t_leaf("int")
    _emit(
        lo,
        node,
        [
            f"{total} = 0",
            f"{sst} = 0",
            f"{i} = 0",
            f"while {i} < {n['id']}:",
            f"    {x} = {seq['id']}[{i}]",
            f"    {total} = {total} + {x}",
            f"    {sst} = {sst} + {x} * {x}",
            f"    {i} = {i} + 1",
        ],
        span,
    )
    if attr in {"pvariance", "pstdev"}:
        lines = [
            f"{num} = {n['id']} * {sst} - {total} * {total}",
            f"{den} = {n['id']} * {n['id']}",
        ]
    else:
        lines = [
            f"if {n['id']} < 2:",
            f"    raise ValueError('{attr} requires at least two data points')",
            f"{num} = {n['id']} * {sst} - {total} * {total}",
            f"{den} = {n['id']} * ({n['id']} - 1)",
        ]
    result = _tname(lo, "v")
    lo.locals[result] = _t_leaf("float")
    lines.append(f"{result} = {num} / {den}")
    _emit(lo, node, lines, span)
    return _nm(result, _t_leaf("float"), span)


def _stdevish(
    lo: Any, node: ast.Call, span: dict[str, Any], arg: dict[str, Any], attr: str, kind: str
) -> dict[str, Any]:
    """sqrt of the variance fold. The CPython exact-rational sqrt is a
    109-bit correctly-rounded integer sqrt (D55): one ulp of difference in
    a minority of int datasets, the same double arithmetic as float data."""

    var = _varianceish(lo, node, span, arg, attr, kind)
    result = _tname(lo, "r")
    lo.locals[result] = _t_leaf("float")
    _emit(lo, node, [f"{result} = {var['id']} ** 0.5"], span)
    return _nm(result, _t_leaf("float"), span)


def _quantiles(lo: Any, node: ast.Call, span: dict[str, Any], arg: dict[str, Any], kind: str) -> dict[str, Any]:
    n = 4
    for kw in node.keywords:
        if kw.arg == "n":
            if not isinstance(kw.value, ast.Constant) or not isinstance(kw.value.value, int):
                lo.fail(kw.value, "PS2003", "statistics.quantiles n= takes an int literal")
            n = kw.value.value
    if n not in {2, 4, 8}:
        lo.fail(node, "PS2003", "statistics.quantiles n= is one of 2, 4, 8")
    seq, cnt = _sorted_copy(lo, node, span, arg, kind, _SORTED_HINT["quantiles"])
    out = _tname(lo, "qs")
    lo.locals[out] = _t_list(_t_leaf("float"))
    i = _tname(lo, "i")
    lo.locals[i] = _t_leaf("int")
    idx = _tname(lo, "p")
    lo.locals[idx] = _t_leaf("int")
    delta = _tname(lo, "d")
    lo.locals[delta] = _t_leaf("int")
    m = _tname(lo, "m")
    lo.locals[m] = _t_leaf("int")
    a = _tname(lo, "a")
    lo.locals[a] = _t_leaf(kind)
    b = _tname(lo, "b")
    lo.locals[b] = _t_leaf(kind)
    # The CPython exclusive method: m = ld + 1, each cut i rescales to
    # m/n with an exact integer delta and integer weights over n.
    _emit(
        lo,
        node,
        [
            f"{out} = []",
            f"{m} = {cnt['id']} + 1",
            f"{i} = 1",
            f"while {i} < {n}:",
            f"    {idx} = {i} * {m} // {n}",
            f"    if {idx} < 1:",
            f"        {idx} = 1",
            f"    if {idx} > {cnt['id']} - 1:",
            f"        {idx} = {cnt['id']} - 1",
            f"    {delta} = {i} * {m} - {idx} * {n}",
            f"    {a} = {seq['id']}[{idx} - 1]",
            f"    {b} = {seq['id']}[{idx}]",
            f"    {out}.append(({a} * ({n} - {delta}) + {b} * {delta}) / {n})",
            f"    {i} = {i} + 1",
        ],
        span,
    )
    return _nm(out, _t_list(_t_leaf("float")), span)
