"""bisect call lowering: bisect_left/right and insort*."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.lower._core import _const_none
from pyscriptc.restlower._shared import _asg, _emit, _nm, _tname

_ORDERABLE = frozenset({"int", "float", "str"})
_INSORT = frozenset({"insort", "insort_left", "insort_right"})


def _bisect_keywords(lo: Any, node: ast.Call, attr: str) -> None:
    """`key=` is a named PS2001; `lo=`/`hi=` stay out of the M1 surface."""

    for keyword in node.keywords:
        name = keyword.arg
        if name == "key":
            lo.fail(node, "PS2001", f"bisect.{attr} key= is not lowered")
        if name in {"lo", "hi"}:
            lo.fail(node, "PS2003", f"bisect.{attr} lo= and hi= are not lowered")
        lo.fail(node, "PS2003", f"bisect.{attr} keyword {name} is not lowered")


def _bisect_operands(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pin the haystack and the key; both must share an orderable element type."""

    _bisect_keywords(lo, node, attr)
    if len(node.args) != 2:
        lo.fail(node, "PS2003", f"bisect.{attr} expects 2 argument(s)")
    seq_val = lo._expr(node.args[0])
    key_val = lo._expr(node.args[1])
    seq_name = _tname(lo, "bs")
    key_name = _tname(lo, "bk")
    lo.locals[seq_name] = seq_val["type"]
    lo.locals[key_name] = key_val["type"]
    lo.prelude.append(_asg(seq_name, seq_val, span))
    lo.prelude.append(_asg(key_name, key_val, span))
    seq = _nm(seq_name, seq_val["type"], span)
    key = _nm(key_name, key_val["type"], span)
    if seq["type"]["kind"] != "list":
        lo.fail(node, "PS1003", f"bisect.{attr} takes a list")
    elem = seq["type"]["elem"]
    if elem["kind"] not in _ORDERABLE:
        lo.fail(node, "PS1003", f"bisect.{attr} takes a list of int, float, or str")
    if key["type"]["kind"] != elem["kind"]:
        lo.fail(node, "PS1003", f"bisect.{attr} key matches the list element")
    return seq, key


def _bisect_index(
    lo: Any, node: ast.Call, span: dict[str, Any], attr: str, seq: dict[str, Any], key: dict[str, Any], left: bool
) -> dict[str, Any]:
    """The insertion index of `key` in `seq`: C for `list[int]`, a scan otherwise."""

    if seq["type"]["elem"]["kind"] == "int":
        symbol = "ps_bisect_left" if left else "ps_bisect_right"
        found = lo._abi_call("bisect", attr, [seq, key], {"kind": "int"}, symbol, span)
        name = _tname(lo, "bi")
        lo.locals[name] = {"kind": "int"}
        lo.prelude.append(_asg(name, found, span))
        return _nm(name, {"kind": "int"}, span)
    index = _tname(lo, "bi")
    bound = _tname(lo, "bn")
    lo.locals[index] = {"kind": "int"}
    lo.locals[bound] = {"kind": "int"}
    op = "<" if left else "<="
    _emit(
        lo,
        node,
        [
            f"{index} = 0",
            f"{bound} = len({seq['id']})",
            f"while {index} < {bound}:",
            f"    if {seq['id']}[{index}] {op} {key['id']}:",
            f"        {index} = {index} + 1",
            "    else:",
            "        break",
        ],
        span,
    )
    return _nm(index, {"kind": "int"}, span)


def _insort(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """`insort*` is `list.insert` at the matching `bisect*` index; CPython returns None."""

    seq, key = _bisect_operands(lo, node, span, attr)
    left = attr == "insort_left"
    index = _bisect_index(lo, node, span, attr, seq, key, left)
    _emit(lo, node, [f"{seq['id']}.insert({index['id']}, {key['id']})"], span)
    return _const_none(span)


def _bisect(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    if attr in _INSORT:
        return _insort(lo, node, span, attr)
    seq, key = _bisect_operands(lo, node, span, attr)
    return _bisect_index(lo, node, span, attr, seq, key, left=attr == "bisect_left")
