"""The pyscriptc_ndarray lowering (issue #496, M1).

The module name is the surface numpy spells, minus everything outside the
closed subset of the issue: creation from a shape or a list, the four
numeric dtypes, elementwise arithmetic between two same-shaped arrays or an
array and a scalar, whole-array reductions, the shape operations, step
slicing, boolean-mask indexing, and the list/bytes conversions. Everything
else fences with PS2001 naming this module — never a silent approximation.

The array is a flat buffer of fixed-width elements; the arithmetic is the
C loop in `runtime/ps_ndarray.c`. This file only checks shapes and dtypes
and hands the IR the symbol the emitter renders as the runtime call.
"""

from __future__ import annotations

import ast
from typing import Any

# The dtypes of this subset, in the order the runtime's dtype codes spell.
_DTYPES: dict[str, int] = {
    "int32": 0,
    "int64": 1,
    "float32": 2,
    "float64": 3,
}

# The IR type of an ndarray value: the class carrier the emitter rides as a
# ptr, with the dtype in the type so the lowering can type-check two arrays
# without a runtime check.
def _nd_type(dtype: str) -> dict[str, Any]:
    return {"kind": "class", "name": "ndarray", "dtype": dtype}


def _dtype_of(typ: dict[str, Any]) -> str:
    return str(typ.get("dtype", "float64"))


def _is_nd(typ: dict[str, Any]) -> bool:
    return typ.get("kind") == "class" and typ.get("name") == "ndarray"


def _ndarray_call(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None, attr: str
) -> dict[str, Any]:
    """The module-level entry points."""
    if attr == "array":
        return _array(lo, node, span)
    if attr == "zeros":
        return _filled(lo, node, span, "zeros", 0.0)
    if attr == "ones":
        return _filled(lo, node, span, "ones", 1.0)
    if attr == "full":
        return _full(lo, node, span)
    if attr == "arange":
        return _arange(lo, node, span)
    if attr == "linspace":
        return _linspace(lo, node, span)
    if attr == "concatenate":
        return _pair(lo, node, span, "concatenate", "ps_nd_concatenate")
    if attr == "stack":
        return _pair(lo, node, span, "stack", "ps_nd_stack")
    lo.fail(node, "PS2001", f"pyscriptc_ndarray.{attr} is not in the lowered subset of pyscriptc_ndarray")
    raise AssertionError("unreachable")


def _dtype_arg(lo: Any, node: ast.Call, value: ast.expr, default: str) -> str:
    """The `dtype=` argument: one of the four names, as a constant."""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        name = value.value
        if name not in _DTYPES:
            lo.fail(node, "PS2003", f"pyscriptc_ndarray dtype {name} is not in the subset")
        return name
    if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) and value.value.id == "np":
        # The `np.int32` spelling: the same four names ride the attribute.
        if value.attr in _DTYPES:
            return value.attr
        lo.fail(node, "PS2003", f"pyscriptc_ndarray dtype np.{value.attr} is not in the subset")
    if value is node.args[0] and isinstance(value, ast.Constant) and isinstance(value.value, str):
        return _dtype_arg(lo, node, value, default)
    lo.fail(node, "PS2003", "pyscriptc_ndarray dtype is one of 'int32', 'int64', 'float32', 'float64'")
    raise AssertionError("unreachable")


def _array(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`array(dtype, data)`: the dtype is the first argument, the data is a
    flat list of numbers."""
    if len(node.args) != 2 or node.keywords:
        lo.fail(node, "PS2003", "pyscriptc_ndarray.array expects (dtype, data)")
    dtype = _dtype_arg(lo, node, node.args[0], "float64")
    data = lo._expr(node.args[1])
    if data["type"]["kind"] != "list" or data["type"]["elem"]["kind"] not in {"int", "float"}:
        lo.fail(node, "PS1003", "pyscriptc_ndarray.array data is a flat list of numbers")
    is_int = data["type"]["elem"]["kind"] == "int"
    symbol = "ps_nd_from_seq"
    args = [
        {"kind": "const", "value": str(_DTYPES[dtype]), "type": {"kind": "int"}, "span": span},
        data,
        {"kind": "const", "value": "1" if is_int else "0", "type": {"kind": "int"}, "span": span},
    ]
    expr = lo._abi_call("pyscriptc_ndarray", "array", args, _nd_type(dtype), symbol, span)
    return expr


def _shape_args(lo: Any, node: ast.Call, span: dict[str, Any], what: str) -> list[dict[str, Any]]:
    """The shape: a list of ints, or one or more int arguments. The shape is
    normalized to a list[int] the runtime reads as ndim plus the extents."""
    if len(node.args) == 1 and node.args[0].__class__.__name__ == "List":
        shape_node = node.args[0]
        dims = [lo._expr(elt) for elt in shape_node.elts]
    else:
        dims = [lo._expr(arg) for arg in node.args]
    if not dims:
        lo.fail(node, "PS2003", f"pyscriptc_ndarray.{what} needs a shape")
    if len(dims) > 4:
        lo.fail(node, "PS2003", f"pyscriptc_ndarray.{what} lowers up to 4 dimensions")
    for dim in dims:
        if dim["type"]["kind"] != "int":
            lo.fail(node, "PS1003", f"pyscriptc_ndarray.{what} shape is a list of ints")
    return dims


def _filled(lo: Any, node: ast.Call, span: dict[str, Any], what: str, value: float) -> dict[str, Any]:
    """`zeros(shape...)` and `ones(shape...)`: the dtype keyword is
    optional and defaults to float64."""
    dtype = "float64"
    args_nodes = list(node.args)
    for keyword in node.keywords:
        if keyword.arg == "dtype":
            dtype = _dtype_arg(lo, node, keyword.value, dtype)
        else:
            lo.fail(node, "PS2003", f"pyscriptc_ndarray.{what} keyword {keyword.arg} is not lowered")
    dims = _shape_args(lo, node, span, what)
    ndim = len(dims)
    call_args = [{"kind": "const", "value": str(_DTYPES[dtype]), "type": {"kind": "int"}, "span": span},
                 {"kind": "const", "value": str(ndim), "type": {"kind": "int"}, "span": span}]
    call_args.extend(dims)
    # The runtime reads the shape through a pointer, so the emitter takes a
    # stack slot: the shape rides as ndim plus a pointer to the extents.
    # The lowering marks the call so the emitter synthesizes the slot.
    expr = lo._abi_call("pyscriptc_ndarray", what, call_args, _nd_type(dtype), f"ps_nd_{what}", span)
    expr["nd_shape"] = ndim
    return expr


def _full(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`full(shape..., value)`: the shape, then the fill value."""
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "pyscriptc_ndarray.full expects (shape, value)")
    dtype = "float64"
    for keyword in node.keywords:
        if keyword.arg == "dtype":
            dtype = _dtype_arg(lo, node, keyword.value, dtype)
        else:
            lo.fail(node, "PS2003", f"pyscriptc_ndarray.full keyword {keyword.arg} is not lowered")
    fill = lo._expr(node.args[1])
    if fill["type"]["kind"] not in {"int", "float"}:
        lo.fail(node, "PS1003", "pyscriptc_ndarray.full value is a number")
    fill_node = node.args[1]
    # A shape literal is a list of ints; the value is the last argument.
    args_nodes = [node.args[0]]
    if isinstance(node.args[0], ast.List):
        dims = [lo._expr(elt) for elt in node.args[0].elts]
    else:
        # The one-argument-per-dimension spelling needs the fill value to
        # be the last argument, so every argument but the last is a dim.
        dims = [lo._expr(arg) for arg in args_nodes[:-1]]
        if not dims:
            lo.fail(node, "PS2003", "pyscriptc_ndarray.full needs a shape")
    if len(dims) > 4:
        lo.fail(node, "PS2003", "pyscriptc_ndarray.full lowers up to 4 dimensions")
    for dim in dims:
        if dim["type"]["kind"] != "int":
            lo.fail(node, "PS1003", "pyscriptc_ndarray.full shape is a list of ints")
    ndim = len(dims)
    call_args = [{"kind": "const", "value": str(_DTYPES[dtype]), "type": {"kind": "int"}, "span": span},
                 {"kind": "const", "value": str(ndim), "type": {"kind": "int"}, "span": span}]
    call_args.extend(dims)
    call_args.append(fill)
    expr = lo._abi_call("pyscriptc_ndarray", "full", call_args, _nd_type(dtype), "ps_nd_full", span)
    expr["nd_shape"] = ndim
    return expr


def _arange(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`arange(start, stop, step)` or `arange(stop)`: the dtype keyword is
    optional and defaults to float64."""
    dtype = "float64"
    for keyword in node.keywords:
        if keyword.arg == "dtype":
            dtype = _dtype_arg(lo, node, keyword.value, dtype)
        else:
            lo.fail(node, "PS2003", f"pyscriptc_ndarray.arange keyword {keyword.arg} is not lowered")
    args = [lo._expr(arg) for arg in node.args]
    if len(args) == 1:
        start = {"kind": "const", "value": "0", "type": {"kind": "int"}, "span": span}
        stop = args[0]
        step = {"kind": "const", "value": "1", "type": {"kind": "int"}, "span": span}
    elif len(args) in {2, 3}:
        start = args[0]
        stop = args[1]
        step = args[2] if len(args) == 3 else {"kind": "const", "value": "1", "type": {"kind": "int"}, "span": span}
    else:
        lo.fail(node, "PS2003", "pyscriptc_ndarray.arange expects 1 to 3 arguments")
    for arg in (start, stop, step):
        if arg["type"]["kind"] not in {"int", "float"}:
            lo.fail(node, "PS1003", "pyscriptc_ndarray.arange arguments are numbers")
    call_args = [{"kind": "const", "value": str(_DTYPES[dtype]), "type": {"kind": "int"}, "span": span},
                 start, stop, step]
    return lo._abi_call("pyscriptc_ndarray", "arange", call_args, _nd_type(dtype), "ps_nd_arange", span)


def _linspace(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`linspace(start, stop, num)`: the endpoint included."""
    dtype = "float64"
    for keyword in node.keywords:
        if keyword.arg == "dtype":
            dtype = _dtype_arg(lo, node, keyword.value, dtype)
        else:
            lo.fail(node, "PS2003", f"pyscriptc_ndarray.linspace keyword {keyword.arg} is not lowered")
    if len(node.args) != 3:
        lo.fail(node, "PS2003", "pyscriptc_ndarray.linspace expects (start, stop, num)")
    args = [lo._expr(arg) for arg in node.args]
    for arg in args:
        if arg["type"]["kind"] not in {"int", "float"}:
            lo.fail(node, "PS1003", "pyscriptc_ndarray.linspace arguments are numbers")
    call_args = [{"kind": "const", "value": str(_DTYPES[dtype]), "type": {"kind": "int"}, "span": span}, *args]
    return lo._abi_call("pyscriptc_ndarray", "linspace", call_args, _nd_type(dtype), "ps_nd_linspace", span)


def _pair(lo: Any, node: ast.Call, span: dict[str, Any], what: str, symbol: str) -> dict[str, Any]:
    """`concatenate(a, b)` and `stack(a, b)`: two arrays whose dtypes and
    trailing shapes match."""
    if len(node.args) != 2 or node.keywords:
        lo.fail(node, "PS2003", f"pyscriptc_ndarray.{what} expects two arrays")
    left = lo._expr(node.args[0])
    right = lo._expr(node.args[1])
    if not _is_nd(left["type"]) or not _is_nd(right["type"]):
        lo.fail(node, "PS1003", f"pyscriptc_ndarray.{what} arguments are arrays")
    if _dtype_of(left["type"]) != _dtype_of(right["type"]):
        lo.fail(node, "PS1003", f"pyscriptc_ndarray.{what} arrays have different dtypes")
    return lo._abi_call("pyscriptc_ndarray", what, [left, right], left["type"], symbol, span)


# ---------------------------------------------------------------------
# the method surface
# ---------------------------------------------------------------------

def _ndarray_method(
    lo: Any, node: ast.Call, span: dict[str, Any], recv: dict[str, Any], attr: str
) -> dict[str, Any] | None:
    """The ndarray method calls, or None when `attr` is not a method.

    The caller (lower._calls) routes the receiver's method call here before
    the class-method table; the return is the same shape `_synth_call`
    produces.
    """
    if attr not in _METHODS:
        return None
    return _method(lo, node, span, recv, attr)


# The method names of the subset. A name outside this set fences.
_METHODS = frozenset(
    {
        "sum",
        "min",
        "max",
        "mean",
        "std",
        "var",
        "argmin",
        "argmax",
        "copy",
        "ravel",
        "transpose",
        "reshape",
        "astype",
        "tolist",
        "tobytes",
    }
)


def _method(
    lo: Any, node: ast.Call, span: dict[str, Any], recv: dict[str, Any], attr: str
) -> dict[str, Any]:
    """The ndarray method calls."""
    dtype = _dtype_of(recv["type"])
    if attr in {"sum", "min", "max", "mean", "std", "var"}:
        return _reduce(lo, node, span, recv, attr, dtype)
    if attr == "argmin":
        return lo._abi_call("pyscriptc_ndarray", "argmin", [recv], _nd_type("int64"), "ps_nd_argmin", span)
    if attr == "argmax":
        return lo._abi_call("pyscriptc_ndarray", "argmax", [recv], _nd_type("int64"), "ps_nd_argmax", span)
    if attr == "copy":
        return lo._abi_call("pyscriptc_ndarray", "copy", [recv], recv["type"], "ps_nd_copy", span)
    if attr == "ravel":
        out = _nd_type(dtype)
        return lo._abi_call("pyscriptc_ndarray", "ravel", [recv], out, "ps_nd_ravel", span)
    if attr == "transpose":
        return lo._abi_call("pyscriptc_ndarray", "transpose", [recv], recv["type"], "ps_nd_transpose", span)
    if attr == "reshape":
        return _reshape(lo, node, span, recv, dtype)
    if attr == "astype":
        return _astype(lo, node, span, recv)
    if attr == "tolist":
        return _tolist(lo, node, span, recv, dtype)
    if attr == "tobytes":
        return lo._abi_call(
            "pyscriptc_ndarray", "tobytes", [recv], {"kind": "bytes"}, "ps_nd_tobytes", span
        )
    return None


def _reduce(lo: Any, node: ast.Call, span: dict[str, Any], recv: dict[str, Any], attr: str, dtype: str) -> dict[str, Any]:
    """`sum`/`min`/`max`/`mean`/`std`/`var` over the whole array, or over
    one axis. The result of a whole-array reduction is a 0-d array; the
    result of an axis reduction drops the axis."""
    axis = None
    args_nodes = list(node.args)
    for keyword in node.keywords:
        if keyword.arg == "axis":
            axis = lo._expr(keyword.value)
        else:
            lo.fail(node, "PS2003", f"pyscriptc_ndarray.{attr} keyword {keyword.arg} is not lowered")
    if len(args_nodes) == 1:
        if axis is not None:
            lo.fail(node, "PS2003", f"pyscriptc_ndarray.{attr} takes the axis once")
        axis = lo._expr(args_nodes[0])
    if axis is None:
        args = [recv, {"kind": "const", "value": "0", "type": {"kind": "int"}, "span": span},
                {"kind": "const", "value": "0", "type": {"kind": "int"}, "span": span}]
    else:
        if axis["type"]["kind"] != "int":
            lo.fail(node, "PS1003", f"pyscriptc_ndarray.{attr} axis is an int")
        args = [recv, axis, {"kind": "const", "value": "1", "type": {"kind": "int"}, "span": span}]
    return lo._abi_call("pyscriptc_ndarray", attr, args, recv["type"], f"ps_nd_{attr}", span)


def _reshape(lo: Any, node: ast.Call, span: dict[str, Any], recv: dict[str, Any], dtype: str) -> dict[str, Any]:
    """`reshape(shape...)`: the same count under new extents."""
    if len(node.args) == 1 and isinstance(node.args[0], ast.List):
        dims = [lo._expr(elt) for elt in node.args[0].elts]
    else:
        dims = [lo._expr(arg) for arg in node.args]
    if not dims:
        lo.fail(node, "PS2003", "pyscriptc_ndarray.reshape needs a shape")
    if len(dims) > 4:
        lo.fail(node, "PS2003", "pyscriptc_ndarray.reshape lowers up to 4 dimensions")
    for dim in dims:
        if dim["type"]["kind"] != "int":
            lo.fail(node, "PS1003", "pyscriptc_ndarray.reshape shape is a list of ints")
    args = [recv, {"kind": "const", "value": str(len(dims)), "type": {"kind": "int"}, "span": span}, *dims]
    expr = lo._abi_call("pyscriptc_ndarray", "reshape", args, recv["type"], "ps_nd_reshape", span)
    expr["nd_shape"] = len(dims)
    return expr


def _astype(lo: Any, node: ast.Call, span: dict[str, Any], recv: dict[str, Any]) -> dict[str, Any]:
    """`astype(dtype)`: the values under a new dtype."""
    if len(node.args) != 1 or node.keywords:
        lo.fail(node, "PS2003", "pyscriptc_ndarray.astype expects one dtype")
    dtype = _dtype_arg(lo, node, node.args[0], "float64")
    args = [recv, {"kind": "const", "value": str(_DTYPES[dtype]), "type": {"kind": "int"}, "span": span}]
    return lo._abi_call("pyscriptc_ndarray", "astype", args, _nd_type(dtype), "ps_nd_astype", span)


def _tolist(lo: Any, node: ast.Call, span: dict[str, Any], recv: dict[str, Any], dtype: str) -> dict[str, Any]:
    """`tolist()`: the nested list of Python values. The element type rides
    on the array's dtype."""
    if node.args or node.keywords:
        lo.fail(node, "PS2003", "pyscriptc_ndarray.tolist takes no arguments")
    elem: dict[str, Any]
    if dtype in {"int32", "int64"}:
        elem = {"kind": "int"}
    elif dtype == "bool":
        elem = {"kind": "bool"}
    else:
        elem = {"kind": "float"}
    out: dict[str, Any]
    if recv["type"].get("ndim", 1) <= 1:
        out = {"kind": "list", "elem": elem}
    else:
        out = {"kind": "list", "elem": {"kind": "list", "elem": elem}}
    return lo._abi_call("pyscriptc_ndarray", "tolist", [recv], out, "ps_nd_tolist", span)
