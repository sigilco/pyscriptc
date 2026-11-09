"""operator, bisect and posixpath call lowering."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import (
    _BIN,
    _CMP,
    _binary,
    _node_type,
    _node_type_kind,
    _t_leaf,
    _type_kind,
)


def _bisect(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    lo._reject_keywords(node)
    if len(node.args) != 2:
        lo.fail(node, "PS2003", f"bisect.{attr} expects 2 argument(s)")
    seq = lo._expr(node.args[0])
    key = lo._expr(node.args[1])
    if _node_type_kind(seq) != "list" or _type_kind(_node_type(seq)["elem"]) != "int":
        lo.fail(node, "PS1003", f"bisect.{attr} takes a list[int]")
    if _node_type_kind(key) != "int":
        lo.fail(node, "PS1003", f"bisect.{attr} key is int")
    symbol = "ps_bisect_left" if attr == "bisect_left" else "ps_bisect_right"
    return lo._abi_call("bisect", attr, [seq, key], _t_leaf("int"), symbol, span)


def _join(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if not 1 <= len(node.args) <= 8:
        lo.fail(node, "PS2003", "posixpath.join expects 1 to 8 argument(s)")
    args = []
    for arg_node in node.args:
        arg = lo._expr(arg_node)
        if _node_type_kind(arg) != "str":
            lo.fail(node, "PS1003", "posixpath.join arguments are str")
        args.append(arg)
    return lo._abi_call("posixpath", "join", args, _t_leaf("str"), "ps_path_join", span)


def _operator(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    if attr == "neg":
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "operator.neg expects 1 argument(s)")
        fake = ast.UnaryOp(op=ast.USub(), operand=node.args[0])
        ast.copy_location(fake, node)
        return lo._unary(fake, span)
    if attr in _CMP:
        if len(node.args) != 2:
            lo.fail(node, "PS2003", f"operator.{attr} expects 2 argument(s)")
        fake = ast.Compare(left=node.args[0], ops=[_CMP[attr]()], comparators=[node.args[1]])
        ast.copy_location(fake, node)
        return lo._compare(fake, span)
    if attr in _BIN:
        if len(node.args) != 2:
            lo.fail(node, "PS2003", f"operator.{attr} expects 2 argument(s)")
        fake = ast.BinOp(left=node.args[0], op=_BIN[attr](), right=node.args[1])
        ast.copy_location(fake, node)
        return lo._binary(fake, span)
    if attr == "concat":
        if len(node.args) != 2:
            lo.fail(node, "PS2003", "operator.concat expects 2 argument(s)")
        left = lo._expr(node.args[0])
        right = lo._expr(node.args[1])
        left_kind = _node_type_kind(left)
        if left_kind == _node_type_kind(right) and left_kind in {"str", "bytes"}:
            return _binary("+", left, right, _node_type(left), span)
        lo.fail(node, "PS2004", "operator.concat takes str or bytes")
    lo.fail(node, "PS2001", f"operator.{attr} is not in the lowered subset of operator")
    raise AssertionError("unreachable")
