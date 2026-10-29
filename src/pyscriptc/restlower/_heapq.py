"""heapq call lowering.

The closed subset is `heapify`, `heappush`, `heappop`, `nlargest` and
`nsmallest` over a `list[int]`. The heap lives in the list itself: the
runtime moves the cells in place, the way CPython's `_heapq` module does,
so `heapify` leaves the same array layout CPython leaves.

The `*_max` family, `heapreplace`, `heappushpop` and `merge` stay fenced.
The `key=` keyword is not lowered: the runtime sorts the raw cells, so a
key function would change the order the comparison sees.
"""

from __future__ import annotations

import ast
from typing import Any


def _int_list(lo: Any, node: ast.AST, value: dict[str, Any], what: str) -> dict[str, Any]:
    """The heap is a `list[int]`: the runtime sorts the cells as int64."""
    typ = value["type"]
    if typ["kind"] != "list" or typ["elem"]["kind"] != "int":
        lo.fail(node, "PS1003", f"{what} takes a list[int]")
    return value


def _heapq_call(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """The heapq calls. `heapify` and `heappush` mutate the list in place
    and return None; `heappop` returns the smallest element; `nlargest` and
    `nsmallest` return a fresh `list[int]`."""
    lo._reject_keywords(node)
    if attr == "heapify":
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "heapq.heapify expects 1 argument(s)")
        heap = _int_list(lo, node, lo._expr(node.args[0]), "heapq.heapify")
        return lo._abi_call("heapq", "heapify", [heap], {"kind": "none"}, "ps_heap_heapify", span)
    if attr == "heappush":
        if len(node.args) != 2:
            lo.fail(node, "PS2003", "heapq.heappush expects 2 argument(s)")
        heap = _int_list(lo, node, lo._expr(node.args[0]), "heapq.heappush")
        item = lo._expr(node.args[1])
        if item["type"]["kind"] != "int":
            lo.fail(node, "PS1003", "heapq.heappush item is int")
        return lo._abi_call("heapq", "heappush", [heap, item], {"kind": "none"}, "ps_heap_heappush", span)
    if attr == "heappop":
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "heapq.heappop expects 1 argument(s)")
        heap = _int_list(lo, node, lo._expr(node.args[0]), "heapq.heappop")
        return lo._abi_call("heapq", "heappop", [heap], {"kind": "int"}, "ps_heap_heappop", span)
    if attr in {"nlargest", "nsmallest"}:
        if len(node.args) != 2:
            lo.fail(node, "PS2003", f"heapq.{attr} expects 2 argument(s)")
        count = lo._expr(node.args[0])
        if count["type"]["kind"] != "int":
            lo.fail(node, "PS1003", f"heapq.{attr} count is int")
        items = _int_list(lo, node, lo._expr(node.args[1]), f"heapq.{attr}")
        symbol = "ps_heap_nlargest" if attr == "nlargest" else "ps_heap_nsmallest"
        return lo._abi_call("heapq", attr, [count, items], {"kind": "list", "elem": {"kind": "int"}}, symbol, span)
    lo.fail(node, "PS2001", f"heapq.{attr} is not in the lowered subset of heapq")
    raise AssertionError("unreachable")
