"""Scalar and introspection builtins."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.lower._core import _const_bool
from pyscriptc.reflection import getattr_note, type_read_note, type_synthesis_note
from pyscriptc.restlower._folds import _STATIC_MISSING, _static_builtin_fold, _static_scalar
from pyscriptc.restlower._shared import (
    _binary,
    _const_text,
    _field,
    _flag,
    _node_id,
    _node_type,
    _node_type_kind,
    _node_value,
    _pin,
    _span_to,
    _t_leaf,
    _type_kind,
    _type_name,
    _type_value,
)


def _isinstance(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "isinstance expects 2 argument(s)")
    obj = lo._expr(node.args[0])
    want = lo._isinstance_type(node.args[1])
    operand = _node_type(obj)
    if _flag(operand, "optional"):
        # `isinstance(x, T)` on `T | None` is the tag: the value is exactly
        # its element type or it is None.
        if lo._isinstance_holds(operand["elem"], want):
            return _field(obj, "_tag", _t_leaf("bool"), span)
        return _const_bool(False, span)
    if _type_kind(operand) == "exc":
        # `isinstance(e, Exc)` on a caught exception: the runtime parent chain
        # decides, because the caught value carries a code, not a static type.
        if _type_kind(want) != "class":
            lo.fail(node, "PS2001", "isinstance type is not in the lowered subset")
        name = _type_name(want)
        code = lo._exception_code_of(name)
        if code is None:
            # A class that is not an exception: no exception instance matches it.
            return _const_bool(False, span)
        return lo._abi_call("builtins", "exc.isa", [obj], _t_leaf("bool"), f"ps_exc_isa_value:{code}", span)
    flag = lo._isinstance_holds(operand, want)
    return _const_bool(flag, span)


def _getattr(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "getattr expects 2 argument(s)")
    obj = lo._expr(node.args[0])
    name = _literal_name(lo, node)
    return lo._lookup_attr(obj, name, span, node)


def _hasattr(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "hasattr expects 2 argument(s)")
    obj = lo._expr(node.args[0])
    name = _literal_name(lo, node)
    return _const_bool(lo._has_attr(obj, name), span)


def _literal_name(lo: Any, node: ast.Call) -> str:
    arg = node.args[1]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.isidentifier():
        return arg.value
    builtin = node.func.id if isinstance(node.func, ast.Name) else "getattr"
    lo.fail(node, "PS2001", getattr_note(builtin))
    raise AssertionError("unreachable")


def _abs(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "abs expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    folded = _static_builtin_fold("abs", arg, span, fence=lo)
    if folded is not None:
        return folded
    kind = _node_type_kind(arg)
    if kind == "int":
        return lo._abi_call("builtins", "abs", [arg], _t_leaf("int"), "ps_abs_i64", span)
    if kind == "float":
        return lo._abi_call("builtins", "abs", [arg], _t_leaf("float"), "ps_abs_f64", span)
    lo.fail(node, "PS2003", "abs is lowered for int and float")
    raise AssertionError("unreachable")


def _divmod(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """divmod(a, b) is the `(a // b, a % b)` pair as a synthetic tuple_2.

    Each operand pins once because it appears in both halves; the halves ride
    the existing `//` and `%` lowering, so int, float, and mixed pairs all
    work, and bool or text operands keep their binop fences.
    """

    lo._reject_keywords(node)
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "divmod expects 2 argument(s)")
    pinned = [_pin(lo, lo._expr(arg), span, "dv") for arg in node.args]
    left = ast.Name(id=_node_id(pinned[0]), ctx=ast.Load())
    right = ast.Name(id=_node_id(pinned[1]), ctx=ast.Load())
    elems = []
    for op in (ast.FloorDiv(), ast.Mod()):
        elems.append(lo._expr(_span_to(node, ast.BinOp(left=left, op=op, right=right))))
    return lo._materialize_tuple(elems, span)


_BASE_SYMS = {"hex": "ps_hex_i64", "oct": "ps_oct_i64", "bin": "ps_bin_i64"}


def _base_repr(lo: Any, node: ast.Call, span: dict[str, Any], name: str) -> dict[str, Any]:
    """hex/oct/bin of an int: CPython's `0x`/`0o`/`0b` spellings, sign first."""

    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", f"{name} expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    if _node_type_kind(arg) != "int":
        lo.fail(node, "PS1003", f"{name} argument is int")
    return lo._abi_call("builtins", name, [arg], _t_leaf("str"), _BASE_SYMS[name], span)


def _pow_call(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """pow(x, y) is `x ** y`; pow(x, y, m) is `x ** y % m` for ints.

    A negative exponent with a modulus keeps the `**` ValueError instead of
    computing CPython's modular inverse.
    """

    lo._reject_keywords(node)
    if len(node.args) not in {2, 3}:
        lo.fail(node, "PS2003", "pow expects 2 or 3 argument(s)")
    base = lo._pow(node, lo._expr(node.args[0]), lo._expr(node.args[1]), span)
    if len(node.args) == 2:
        return base
    mod = lo._expr(node.args[2])
    if _node_type_kind(base) != "int" or _node_type_kind(mod) != "int":
        lo.fail(node, "PS2003", "pow with a modulus takes int arguments")
    return _binary("%", base, mod, _t_leaf("int"), span)


def _type_call(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """type(e) on a caught exception: the runtime class name as a str carrier.

    CPython returns the class object; the surface this subset needs is
    `type(e).__name__`, so `__name__` on this carrier is the carrier itself
    (see `_lookup_attr`). Any other use of the carrier is not a str.
    """

    lo._reject_keywords(node)
    if len(node.args) != 1:
        if len(node.args) == 3:
            lo.fail(node, "PS2003", type_synthesis_note())
        lo.fail(node, "PS2003", "type expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    if _node_type_kind(arg) != "exc":
        lo.fail(node, "PS2002", type_read_note())
    expr = lo._abi_call("builtins", "exc.name", [arg], _t_leaf("str"), "ps_exc_class", span)
    expr["attrtype"] = "type"
    return expr


def _repr(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """repr(x) is `'%r' % x` — the format engine already owns the spellings."""

    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "repr expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    folded = _static_builtin_fold("repr", arg, span, fence=lo)
    if folded is not None:
        return folded
    kind = _node_type_kind(arg)
    style = "percent"
    if kind == "exc":
        return lo._abi_call("builtins", "exc.repr", [arg], _t_leaf("str"), "ps_exc_repr", span)
    if kind == "dict":
        value_kinds = {"int", "float", "str", "bytes"}
        dtype = _node_type(arg)
        if _type_kind(dtype["key"]) not in {"int", "str"} or _type_kind(_type_value(dtype)) not in value_kinds:
            lo.fail(node, "PS2002", "repr needs scalar dict keys and values")
        style = "map"
    elif kind == "class":
        called = lo._dunder_call(node, arg, "__repr__", [], span)
        if called is not None:
            return called
        lo.fail(node, "PS2004", f"repr of {kind} is not lowered")
    elif kind not in {"int", "float", "str", "bool", "bytes"}:
        lo.fail(node, "PS2004", f"repr of {kind} is not lowered")
    fmt = _const_text("%r", "str", span)
    expr = lo._abi_call("builtins", "format", [fmt, arg], _t_leaf("str"), "ps_str_format", span)
    expr["format_style"] = style
    return expr


def _ord(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "ord expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    folded = _static_builtin_fold("ord", arg, span, fence=lo)
    if folded is not None:
        return folded
    if _node_type_kind(arg) != "str":
        lo.fail(node, "PS1003", "ord argument is str")
    return lo._abi_call("builtins", "ord", [arg], _t_leaf("int"), "ps_ord", span)


def _chr(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "chr expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    folded = _static_builtin_fold("chr", arg, span, fence=lo)
    if folded is not None:
        return folded
    val = _static_scalar(arg)
    if val is not _STATIC_MISSING and isinstance(val, int) and not isinstance(val, bool):
        if val < 0 or val > 0x10FFFF:
            lo.fail(node, "PS2003", "chr() arg not in range(0x110000)")
    if _node_type_kind(arg) != "int":
        lo.fail(node, "PS1003", "chr argument is int")
    return lo._abi_call("builtins", "chr", [arg], _t_leaf("str"), "ps_chr", span)


def _round(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) not in {1, 2}:
        lo.fail(node, "PS2003", "round expects 1 or 2 argument(s)")
    arg = lo._expr(node.args[0])
    if len(node.args) == 1 and _node_type_kind(arg) == "int":
        return arg
    if _node_type_kind(arg) != "float":
        lo.fail(node, "PS1003", "round argument is float")
    if len(node.args) == 1:
        return lo._abi_call("builtins", "round", [arg], _t_leaf("int"), "ps_round_i64", span)
    digits = lo._expr(node.args[1])
    if _node_type_kind(digits) != "int":
        lo.fail(node, "PS1003", "round ndigits is int")
    folded = _static_builtin_fold("round", digits, span)
    if folded is not None:
        nd = _node_value(folded)
        if isinstance(nd, str) and nd.lstrip("-").isdigit():
            nd = int(nd)
        if isinstance(nd, int) and not 0 <= nd <= 8:
            lo.fail(node, "PS2003", "round ndigits must be between 0 and 8; CPython also rounds by -8 <= ndigits < 0")
    return lo._abi_call("builtins", "round", [arg, digits], _t_leaf("float"), "ps_round_nd", span)
