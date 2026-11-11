"""The ndarray operator lowering (issue #496, M1).

The elementwise operators are the same shape for the arithmetic (`+`, `-`,
`*`, `/`, `//`, `%`) and the comparisons (`<`, `<=`, `>`, `>=`, `==`,
`!=`): two arrays of the same shape, or an array and a Python number.
The comparisons produce a bool array; the arithmetic produces an array
whose dtype follows numpy's promotion rules.

The binary path lives here rather than in `lower/_expr.py` because it is
the one place the compiler's numeric operators meet a container: the
generic path would refuse the array as an unknown class.
"""

from __future__ import annotations

import ast
from typing import Any

# The operator names the runtime symbols spell.
_ARITH: dict[str, str] = {
    "+": "add",
    "-": "sub",
    "*": "mul",
    "/": "div",
    "//": "floordiv",
    "%": "mod",
}

_COMPARE: dict[str, str] = {
    "==": "eq",
    "!=": "ne",
    "<": "lt",
    "<=": "le",
    ">": "gt",
    ">=": "ge",
}


def _is_ndarray(typ: dict[str, Any]) -> bool:
    return typ.get("kind") == "class" and typ.get("name") == "ndarray"


def _nd_type(dtype: str) -> dict[str, Any]:
    """The IR type of an ndarray value; the dtype rides in the label."""
    return {"kind": "class", "name": "ndarray", "label": f"ndarray[{dtype}]"}


def _dtype_of(typ: dict[str, Any]) -> str:
    """The dtype an ndarray IR type carries, read back from its label."""
    label = str(typ.get("label", ""))
    if label.startswith("ndarray[") and label.endswith("]"):
        return label[len("ndarray[") : -1]
    return "float64"


def _ndarray_binary(
    lo: Any, node: ast.BinOp, left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]
) -> dict[str, Any]:
    """The arithmetic between two arrays, or between an array and a number."""
    op = _op_text(node.op)
    symbol = _ARITH.get(op)
    if symbol is None:
        lo.fail(node, "PS2004", f"{op} on a pyscriptc_ndarray array is not lowered")
    left_is_arr = _is_ndarray(left["type"])
    right_is_arr = _is_ndarray(right["type"])
    if left_is_arr and right_is_arr:
        if _dtype_of(left["type"]) != _dtype_of(right["type"]):
            lo.fail(node, "PS1003", "the two arrays have different dtypes")
        return lo._abi_call("pyscriptc_ndarray", symbol, [left, right], left["type"], f"ps_nd_{symbol}", span)
    arr, scalar, arr_first = (left, right, True) if left_is_arr else (right, left, False)
    if not left_is_arr and not right_is_arr:
        lo.fail(node, "PS2004", f"{op} on a pyscriptc_ndarray array is not lowered")
    if scalar["type"]["kind"] not in {"int", "float"}:
        lo.fail(node, "PS1003", f"{op} on a pyscriptc_ndarray array and a number")
    # The scalar's strength decides the runtime path: a Python int is weak
    # (the array's dtype wins), a Python float lifts an integer array.
    suffix = "f" if scalar["type"]["kind"] == "float" else "s"
    args = [arr, scalar] if arr_first else [scalar, arr]
    if not arr_first:
        # The runtime's scalar path takes the array first, so the operand
        # order is normalized here: `2 - a` is `a` subtracted from 2, which
        # the runtime's sub path does not model. That spelling stays
        # fenced rather than approximated.
        lo.fail(node, "PS2004", f"{op} with the array on the right is not lowered")
    return lo._abi_call("pyscriptc_ndarray", symbol, args, arr["type"], f"ps_nd_{symbol}_{suffix}", span)


def _ndarray_compare(
    lo: Any,
    node: ast.AST,
    op: str,
    left: dict[str, Any],
    right: dict[str, Any],
    span: dict[str, Any],
) -> dict[str, Any]:
    """The comparison between two arrays, or between an array and a number."""
    symbol = _COMPARE.get(op)
    if symbol is None:
        lo.fail(node, "PS2004", f"{op} on a pyscriptc_ndarray array is not lowered")
    left_is_arr = _is_ndarray(left["type"])
    right_is_arr = _is_ndarray(right["type"])
    out = _nd_type("bool")
    if left_is_arr and right_is_arr:
        if _dtype_of(left["type"]) != _dtype_of(right["type"]):
            lo.fail(node, "PS1003", "the two arrays have different dtypes")
        return lo._abi_call("pyscriptc_ndarray", symbol, [left, right], out, f"ps_nd_{symbol}", span)
    if not left_is_arr and not right_is_arr:
        lo.fail(node, "PS2004", f"{op} on a pyscriptc_ndarray array is not lowered")
    arr, scalar, arr_first = (left, right, True) if left_is_arr else (right, left, False)
    if scalar["type"]["kind"] not in {"int", "float"}:
        lo.fail(node, "PS1003", f"{op} on a pyscriptc_ndarray array and a number")
    if not arr_first:
        lo.fail(node, "PS2004", f"{op} with the array on the right is not lowered")
    suffix = "f" if scalar["type"]["kind"] == "float" else "s"
    return lo._abi_call("pyscriptc_ndarray", symbol, [arr, scalar], out, f"ps_nd_{symbol}_{suffix}", span)


def _op_text(op: ast.AST) -> str:
    """The operator's spelling, for the fence message."""
    for name, cls in (
        ("+", ast.Add),
        ("-", ast.Sub),
        ("*", ast.Mult),
        ("/", ast.Div),
        ("//", ast.FloorDiv),
        ("%", ast.Mod),
    ):
        if isinstance(op, cls):
            return name
    return type(op).__name__
