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


def _nd_type(dtype: str, ndim: int = 1) -> dict[str, Any]:
    """The IR type of an ndarray value; the dtype and the rank ride in the
    label, the one part of a type the dict IR round trip keeps."""
    return {"kind": "class", "name": "ndarray", "label": f"ndarray[{dtype},{ndim}]"}


def _dtype_of(typ: dict[str, Any]) -> str:
    """The dtype an ndarray IR type carries, read back from its label."""
    return _label_parts(typ)[0]


def _ndim_of(typ: dict[str, Any]) -> int:
    """The rank an ndarray IR type carries, read back from its label."""
    return _label_parts(typ)[1]


def _label_parts(typ: dict[str, Any]) -> tuple[str, int]:
    label = str(typ.get("label", ""))
    if not (label.startswith("ndarray[") and label.endswith("]")):
        return ("float64", 1)
    body = label[len("ndarray[") : -1]
    parts = body.split(",")
    dtype = parts[0].strip() or "float64"
    ndim = 1
    if len(parts) > 1 and parts[1].strip().isdigit():
        ndim = int(parts[1].strip())
    return (dtype, ndim)


def _promote(left: str, right: str, op: str) -> str:
    """The result dtype of an elementwise array op, numpy's rules.

    Same-dtype arithmetic keeps the dtype; a mixed int/float pair floats
    (float64, or float32 only when both sides are float32); the true divide
    always floats (float32 stays float32, everything else is float64). The
    runtime enforces the same table; this copy exists so the IR names the
    result type the same way it names the call."""
    if op == "/":
        if left == "float32" or right == "float32":
            return "float32"
        return "float64"
    lf = left in {"float32", "float64"}
    rf = right in {"float32", "float64"}
    if lf or rf:
        if left == "float32" and right == "float32":
            return "float32"
        return "float64"
    if left == "int64" or right == "int64":
        return "int64"
    return "int32"


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
        ld = _dtype_of(left["type"])
        rd = _dtype_of(right["type"])
        out = _nd_type(_promote(ld, rd, op), _ndim_of(left["type"]))
        return lo._abi_call("pyscriptc_ndarray", symbol, [left, right], out, f"ps_nd_{symbol}", span)
    arr, scalar, arr_first = (left, right, True) if left_is_arr else (right, left, False)
    if not left_is_arr and not right_is_arr:
        lo.fail(node, "PS2004", f"{op} on a pyscriptc_ndarray array is not lowered")
    if scalar["type"]["kind"] not in {"int", "float"}:
        lo.fail(node, "PS1003", f"{op} on a pyscriptc_ndarray array and a number")
    # The scalar's strength decides the runtime path: a Python int is weak
    # (the array's dtype wins), a Python float lifts an integer array.
    suffix = "f" if scalar["type"]["kind"] == "float" else "s"
    arr_dtype = _dtype_of(arr["type"])
    if op == "/":
        out_dtype = "float32" if arr_dtype == "float32" else "float64"
    elif suffix == "f" and arr_dtype in {"int32", "int64"}:
        # A Python float with an integer array lifts to float64.
        out_dtype = "float64"
    else:
        out_dtype = arr_dtype
    args = [arr, scalar] if arr_first else [scalar, arr]
    if not arr_first:
        # The runtime's scalar path takes the array first, so the operand
        # order is normalized here: `2 - a` is `a` subtracted from 2, which
        # the runtime's sub path does not model. That spelling stays
        # fenced rather than approximated.
        lo.fail(node, "PS2004", f"{op} with the array on the right is not lowered")
    out = _nd_type(out_dtype, _ndim_of(arr["type"]))
    return lo._abi_call("pyscriptc_ndarray", symbol, args, out, f"ps_nd_{symbol}_{suffix}", span)


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
    if left_is_arr and right_is_arr:
        if _dtype_of(left["type"]) != _dtype_of(right["type"]):
            lo.fail(node, "PS1003", "the two arrays have different dtypes")
        out = _nd_type("bool", _ndim_of(left["type"]))
        return lo._abi_call("pyscriptc_ndarray", symbol, [left, right], out, f"ps_nd_{symbol}", span)
    if not left_is_arr and not right_is_arr:
        lo.fail(node, "PS2004", f"{op} on a pyscriptc_ndarray array is not lowered")
    arr, scalar, arr_first = (left, right, True) if left_is_arr else (right, left, False)
    out = _nd_type("bool", _ndim_of(arr["type"]))
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
