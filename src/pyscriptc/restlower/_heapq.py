"""heapq call lowering.

The closed subset is `heapify`, `heappush`, `heappop`, `heappushpop`,
`heapreplace`, `nlargest` and `nsmallest` over a list of one orderable
scalar element type (`int`, `float` or `str`). The heap lives in the list
itself: the emitted block sifts the cells in place with CPython's own
`_siftup`/`_siftdown` algorithm, so `heapify` leaves the same array layout
CPython leaves and a `heappop` sequence pops in CPython's order.

The `*_max` family and `merge` stay fenced, and `key=` is not lowered:
the emitted comparison is the raw `<` on the element type. A tuple
element type is fenced too: ordering comparisons on tuple values are not
in the lowered surface yet, so the pack refuses the call with `PS1003`
naming the element kind it needs.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc import _irnode as _ir
from pyscriptc.restlower._shared import _asg, _emit, _mk_span, _node_type, _nm, _t_leaf, _t_list, _tname
from pyscriptc.tirserde import node_to_dict

_HEAP_ELEM = {"int", "float", "str"}


def _none(span: dict[str, Any]) -> dict[str, Any]:
    """The `None` const node a None-returning call spells."""
    return node_to_dict(_ir.const_node(_ir.P_NONE, "", _ir.t_leaf("none"), _mk_span(span)))


def _heap_list(lo: Any, node: ast.AST, value: dict[str, Any], what: str) -> dict[str, Any]:
    """The heap is a list of one orderable scalar element kind: the
    emitted comparison is the raw `<` on that kind."""
    typ = _node_type(value)
    if typ["kind"] != "list" or typ["elem"]["kind"] not in _HEAP_ELEM:
        lo.fail(node, "PS1003", f"{what} takes a list of int, float or str")
    return value


def _heap_item(lo: Any, node: ast.AST, value: dict[str, Any], elem: dict[str, Any], what: str) -> None:
    """The pushed item must carry the heap's element kind."""
    if _node_type(value)["kind"] != elem["kind"]:
        lo.fail(node, "PS1003", f"{what} item is {elem['kind']}")


def _heap_pin(lo: Any, value: dict[str, Any], span: dict[str, Any], hint: str) -> str:
    """Bind the heap argument to a fresh identifier-safe local the emitted
    block reads and mutates; returns the name (`_pin` mints a dotted IR
    temp the generated source cannot spell)."""
    name = _tname(lo, hint)
    lo.locals[name] = _node_type(value)
    lo.prelude.append(_asg(name, value, span))
    return name


def _siftdown(
    pid: str, start: str, pos: str, parent: str, newitem: str, indent: str, maxheap: bool
) -> list[str]:
    """CPython's `_siftdown`: move the held cell up toward the root until it
    fits, shifting parents down. `start` is the root of the subtree,
    `newitem` names the local holding the cell and `maxheap` picks the
    comparison sense (`<` for a min-heap, `>` for a max-heap)."""
    fits = f"{pid}[{parent}] < {newitem}" if maxheap else f"{newitem} < {pid}[{parent}]"
    return [
        f"{indent}while {pos} > {start}:",
        f"{indent}    {parent} = ({pos} - 1) // 2",
        f"{indent}    if {fits}:",
        f"{indent}        {pid}[{pos}] = {pid}[{parent}]",
        f"{indent}        {pos} = {parent}",
        f"{indent}    else:",
        f"{indent}        break",
        f"{indent}{pid}[{pos}] = {newitem}",
    ]


def _siftup(
    pid: str,
    n: str,
    pos: str,
    newitem: str,
    child: str,
    right: str,
    parent: str,
    start: str,
    indent: str,
    maxheap: bool,
) -> list[str]:
    """CPython's `_siftup`: bubble the extreme child up to a leaf, then
    `_siftdown` the displaced cell into place. `n` is the live length and
    `pos` holds the root of the subtree on entry."""
    # CPython's min-heap takes the right child when the left is not
    # strictly smaller; its max-heap takes the right child when the right
    # is not strictly smaller. The tie goes right in both.
    pick = f"not {pid}[{right}] < {pid}[{child}]" if maxheap else f"not {pid}[{child}] < {pid}[{right}]"
    return [
        f"{indent}{newitem} = {pid}[{pos}]",
        f"{indent}{start} = {pos}",
        f"{indent}while True:",
        f"{indent}    {child} = 2 * {pos} + 1",
        f"{indent}    if {child} >= {n}:",
        f"{indent}        break",
        f"{indent}    {right} = {child} + 1",
        f"{indent}    if {right} < {n} and {pick}:",
        f"{indent}        {child} = {right}",
        f"{indent}    {pid}[{pos}] = {pid}[{child}]",
        f"{indent}    {pos} = {child}",
        f"{indent}{pid}[{pos}] = {newitem}",
        *_siftdown(pid, start, pos, parent, newitem, indent, maxheap),
    ]


def _heapify(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`heapify(xs)` sifts the list into a heap in place, returns None."""
    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "heapq.heapify expects 1 argument(s)")
    heap = _heap_list(lo, node, lo._expr(node.args[0]), "heapq.heapify")
    pid = _heap_pin(lo, heap, span, "hq")
    n, i, pos, newitem, child, right, parent, start = (
        _tname(lo, h) for h in ("hqn", "hqi", "hqp", "hqw", "hqc", "hqr", "hqpa", "hqs")
    )
    lo.locals[n] = _t_leaf("int")
    lo.locals[i] = _t_leaf("int")
    for t in (pos, child, right, parent, start):
        lo.locals[t] = _t_leaf("int")
    elem = _node_type(heap)["elem"]
    lo.locals[newitem] = elem
    _emit(
        lo,
        node,
        [
            f"{n} = len({pid})",
            f"{i} = {n} // 2 - 1",
            f"while {i} >= 0:",
            f"    {pos} = {i}",
            *_siftup(pid, n, pos, newitem, child, right, parent, start, "    ", False),
            f"    {i} = {i} - 1",
        ],
        span,
    )
    return _none(span)


def _heappush(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`heappush(xs, x)` appends at the end and sifts the cell up."""
    lo._reject_keywords(node)
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "heapq.heappush expects 2 argument(s)")
    heap = _heap_list(lo, node, lo._expr(node.args[0]), "heapq.heappush")
    item = lo._expr(node.args[1])
    elem = _node_type(heap)["elem"]
    _heap_item(lo, node, item, elem, "heapq.heappush")
    pid = _heap_pin(lo, heap, span, "hq")
    item_id = _heap_pin(lo, item, span, "hqi")
    pos, parent = (_tname(lo, h) for h in ("hqp", "hqpa"))
    lo.locals[pos] = _t_leaf("int")
    lo.locals[parent] = _t_leaf("int")
    _emit(
        lo,
        node,
        [
            f"{pid}.append({item_id})",
            f"{pos} = len({pid}) - 1",
            *_siftdown(pid, "0", pos, parent, item_id, "", False),
        ],
        span,
    )
    return _none(span)


def _heappop(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`heappop(xs)` returns the smallest element: the root, with the last
    cell moved to the top and sifted down."""
    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "heapq.heappop expects 1 argument(s)")
    heap = _heap_list(lo, node, lo._expr(node.args[0]), "heapq.heappop")
    elem = _node_type(heap)["elem"]
    pid = _heap_pin(lo, heap, span, "hq")
    out, n, pos, newitem, child, right, parent, start = (
        _tname(lo, h) for h in ("hqo", "hqn", "hqp", "hqw", "hqc", "hqr", "hqpa", "hqs")
    )
    lo.locals[out] = elem
    lo.locals[n] = _t_leaf("int")
    for t in (pos, child, right, parent, start):
        lo.locals[t] = _t_leaf("int")
    lo.locals[newitem] = elem
    _emit(
        lo,
        node,
        [
            f"{n} = len({pid})",
            f"if {n} <= 0:",
            f"    raise IndexError(\"index out of range\")",
            f"{out} = {pid}[0]",
            f"{n} = {n} - 1",
            f"if {n} > 0:",
            f"    {pid}[0] = {pid}[{n}]",
            f"    {pid}.pop()",
            f"    {pos} = 0",
            *_siftup(pid, n, pos, newitem, child, right, parent, start, "    ", False),
            f"else:",
            f"    {pid}.pop()",
        ],
        span,
    )
    return _nm(out, elem, span)


def _heappushpop(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`heappushpop(xs, x)`: if the new item is larger than the root it
    becomes the root and sifts down, otherwise the root pops unchanged."""
    lo._reject_keywords(node)
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "heapq.heappushpop expects 2 argument(s)")
    heap = _heap_list(lo, node, lo._expr(node.args[0]), "heapq.heappushpop")
    item = lo._expr(node.args[1])
    elem = _node_type(heap)["elem"]
    _heap_item(lo, node, item, elem, "heapq.heappushpop")
    pid = _heap_pin(lo, heap, span, "hq")
    item_id = _heap_pin(lo, item, span, "hqi")
    out, n, pos, newitem, child, right, parent, start = (
        _tname(lo, h) for h in ("hqo", "hqn", "hqp", "hqw", "hqc", "hqr", "hqpa", "hqs")
    )
    lo.locals[out] = elem
    lo.locals[n] = _t_leaf("int")
    for t in (pos, child, right, parent, start):
        lo.locals[t] = _t_leaf("int")
    lo.locals[newitem] = elem
    _emit(
        lo,
        node,
        [
            f"{out} = {item_id}",
            f"if len({pid}) > 0 and {pid}[0] < {item_id}:",
            f"    {out} = {pid}[0]",
            f"    {pid}[0] = {item_id}",
            f"    {n} = len({pid})",
            f"    {pos} = 0",
            *_siftup(pid, n, pos, newitem, child, right, parent, start, "    ", False),
        ],
        span,
    )
    return _nm(out, elem, span)


def _heapreplace(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`heapreplace(xs, x)` pops the root and pushes x in one sift: x
    always lands at the root and sifts down, even when it is smaller than
    the old root (CPython's ordering, kept exactly)."""
    lo._reject_keywords(node)
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "heapq.heapreplace expects 2 argument(s)")
    heap = _heap_list(lo, node, lo._expr(node.args[0]), "heapq.heapreplace")
    item = lo._expr(node.args[1])
    elem = _node_type(heap)["elem"]
    _heap_item(lo, node, item, elem, "heapq.heapreplace")
    pid = _heap_pin(lo, heap, span, "hq")
    item_id = _heap_pin(lo, item, span, "hqi")
    out, n, pos, newitem, child, right, parent, start = (
        _tname(lo, h) for h in ("hqo", "hqn", "hqp", "hqw", "hqc", "hqr", "hqpa", "hqs")
    )
    lo.locals[out] = elem
    lo.locals[n] = _t_leaf("int")
    for t in (pos, child, right, parent, start):
        lo.locals[t] = _t_leaf("int")
    lo.locals[newitem] = elem
    _emit(
        lo,
        node,
        [
            f"{n} = len({pid})",
            f"if {n} <= 0:",
            f"    raise IndexError(\"index out of range\")",
            f"{out} = {pid}[0]",
            f"{pid}[0] = {item_id}",
            f"{pos} = 0",
            *_siftup(pid, n, pos, newitem, child, right, parent, start, "", False),
        ],
        span,
    )
    return _nm(out, elem, span)


def _nlargest_nsmallest(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """`nlargest(k, xs)` / `nsmallest(k, xs)`: the pool is copied, heapified
    over the copy and drained k times, so the result is a fresh list with
    the element type preserved. The comparison sense flips between the two
    names, so one sift body serves both."""
    lo._reject_keywords(node)
    if len(node.args) != 2:
        lo.fail(node, "PS2003", f"heapq.{attr} expects 2 argument(s)")
    count = lo._expr(node.args[0])
    if _node_type(count)["kind"] != "int":
        lo.fail(node, "PS1003", f"heapq.{attr} count is int")
    pool = _heap_list(lo, node, lo._expr(node.args[1]), f"heapq.{attr}")
    elem = _node_type(pool)["elem"]
    pid = _heap_pin(lo, pool, span, "hq")
    count_id = _heap_pin(lo, count, span, "hqn")
    out, copy, k, i, pos, newitem, child, right, parent, start, top, n = (
        _tname(lo, h)
        for h in ("hqo", "hqh", "hqk", "hqi", "hqp", "hqw", "hqc", "hqr", "hqpa", "hqs", "hqt", "hqn2")
    )
    lo.locals[out] = _t_list(elem)
    lo.locals[copy] = _t_list(elem)
    lo.locals[k] = _t_leaf("int")
    lo.locals[i] = _t_leaf("int")
    lo.locals[n] = _t_leaf("int")
    for t in (pos, child, right, parent, start):
        lo.locals[t] = _t_leaf("int")
    lo.locals[newitem] = elem
    lo.locals[top] = elem
    maxheap = attr == "nlargest"
    _emit(
        lo,
        node,
        [
            f"{out} = []",
            f"{k} = {count_id}",
            f"if {k} > 0:",
            f"    {copy} = list({pid})",
            f"    {n} = len({copy})",
            f"    {i} = {n} // 2 - 1",
            f"    while {i} >= 0:",
            f"        {pos} = {i}",
            *_siftup(copy, n, pos, newitem, child, right, parent, start, "        ", maxheap),
            f"        {i} = {i} - 1",
            f"    while {k} > 0 and len({copy}) > 0:",
            f"        {top} = {copy}[0]",
            f"        {n} = len({copy}) - 1",
            f"        if {n} <= 0:",
            f"            {copy}.pop()",
            f"        else:",
            f"            {copy}[0] = {copy}[{n}]",
            f"            {copy}.pop()",
            f"            {pos} = 0",
            *_siftup(copy, n, pos, newitem, child, right, parent, start, "            ", maxheap),
            f"        {out}.append({top})",
            f"        {k} = {k} - 1",
        ],
        span,
    )
    return _nm(out, _t_list(elem), span)


def _heapq_call(lo: Any, node: ast.Call, span: dict[str, Any], expect: Any, attr: str) -> dict[str, Any]:
    """The heapq calls. `heapify`, `heappush`, `heappushpop` and
    `heapreplace` mutate the list in place (the first two return None);
    `heappop` returns the smallest element; `nlargest`/`nsmallest` return
    a fresh list with the element type preserved."""
    if attr == "heapify":
        return _heapify(lo, node, span)
    if attr == "heappush":
        return _heappush(lo, node, span)
    if attr == "heappop":
        return _heappop(lo, node, span)
    if attr == "heappushpop":
        return _heappushpop(lo, node, span)
    if attr == "heapreplace":
        return _heapreplace(lo, node, span)
    if attr in {"nlargest", "nsmallest"}:
        return _nlargest_nsmallest(lo, node, span, attr)
    lo.fail(node, "PS2001", f"heapq.{attr} is not in the lowered subset of heapq")
    raise AssertionError("unreachable")
