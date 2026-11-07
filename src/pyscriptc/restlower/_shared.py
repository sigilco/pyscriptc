"""Leaf helpers shared by the restlower call handlers.

Builders construct typed `_irnode` records and reconstruct the dict IR at
the restlower boundary (`node_to_dict`), so callers keep receiving dicts.
Readers go through the accessors below instead of raw node keys.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc import _irnode as _ir
from pyscriptc.tirserde import dict_to_node, dict_to_span, dict_to_type, node_to_dict

_CMP = {"eq": ast.Eq, "ne": ast.NotEq, "lt": ast.Lt, "le": ast.LtE, "gt": ast.Gt, "ge": ast.GtE}


_BIN = {"add": ast.Add, "sub": ast.Sub, "mul": ast.Mult, "floordiv": ast.FloorDiv, "truediv": ast.Div, "mod": ast.Mod}

_PTAGS = {
    "str": _ir.P_STR,
    "int": _ir.P_INT,
    "float": _ir.P_FLOAT,
    "bool": _ir.P_BOOL,
    "none": _ir.P_NONE,
    "bytes": _ir.P_BYTES,
}


def _mk_span(span: dict[str, Any]) -> Any:
    """The Span record the dict-IR span spells."""

    return dict_to_span(span)


def _type_kind(typ: dict[str, Any]) -> str:
    """The kind tag of a type record. Type dispatch reads the record
    through this accessor instead of the raw dict key."""

    kind: str = typ["kind"]
    return kind


def _node_kind(node: dict[str, Any]) -> str:
    """The kind tag of a dict-IR node. Node dispatch reads the record
    through this accessor instead of the raw dict key."""

    kind: str = node["kind"]
    return kind


def _flag(record: dict[str, Any], name: str) -> bool:
    """The truthiness of an optional bool field on a dict-IR record, or
    False when the field is absent."""

    if name not in record:
        return False
    flag: bool = record[name]
    return flag


def _node_type(node: dict[str, Any]) -> dict[str, Any]:
    """The type record a dict-IR node carries. A consumer of the node's
    type reads it through this accessor instead of the raw dict key."""

    typ: dict[str, Any] = node["type"]
    return typ


def _node_type_kind(node: dict[str, Any]) -> str:
    """The kind tag of the type record a dict-IR node carries."""

    return _type_kind(_node_type(node))


def _node_id(node: dict[str, Any]) -> str:
    """The identifier a name node carries."""

    node_id: str = node["id"]
    return node_id


def _node_elems(node: dict[str, Any]) -> list[dict[str, Any]]:
    """The element list a list node carries."""

    elems: list[dict[str, Any]] = node["elems"]
    return elems


def _node_value(node: dict[str, Any]) -> Any:
    """The payload a const node carries."""

    value: Any = node["value"]
    return value


def _type_value(typ: dict[str, Any]) -> dict[str, Any]:
    """The value type a dict type record carries."""

    value: dict[str, Any] = typ["value"]
    return value


def _type_name(typ: dict[str, Any]) -> str:
    """The class name a class type record carries, or "" when absent."""

    name: str = typ.get("name") or ""
    return name


def _t_leaf(kind: str) -> dict[str, Any]:
    """The dict-IR type one leaf kind spells, through the T record."""

    return node_to_dict(_ir.t_leaf(kind))


def _t_list(elem: dict[str, Any]) -> dict[str, Any]:
    """The dict-IR `list` type one element type spells."""

    return node_to_dict(_ir.t_list(dict_to_type(elem)))


def _list_lit(elems: list[dict[str, Any]], elem: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The dict-IR list literal one element run spells."""

    return node_to_dict(
        _ir.list_lit([dict_to_node(item) for item in elems], _ir.t_list(dict_to_type(elem)), _mk_span(span))
    )


def _if_stmt(
    cond: dict[str, Any], then: list[dict[str, Any]], else_: list[dict[str, Any]], span: dict[str, Any]
) -> dict[str, Any]:
    """The dict-IR if statement one condition and two arms spell."""

    return node_to_dict(
        _ir.if_stmt(
            dict_to_node(cond),
            [dict_to_node(item) for item in then],
            [dict_to_node(item) for item in else_],
            _mk_span(span),
        )
    )


def _call_builtin(name: str, args: list[dict[str, Any]], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The dict-IR builtin call one name, argument run and result type spell."""

    return node_to_dict(
        _ir.call(
            _ir.builtin(name),
            [dict_to_node(arg) for arg in args],
            dict_to_type(typ),
            _mk_span(span),
            "",
            "",
            -1,
        )
    )


def _raise_stmt(exc: str, message: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The dict-IR raise statement one exception name, message and span spell."""

    return node_to_dict(_ir.raise_stmt(exc, _mk_span(span), -1, dict_to_node(message)))


def _expr_stmt(expr: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The dict-IR expression statement one expression and span spell."""

    return node_to_dict(_ir.expr_stmt(dict_to_node(expr), _mk_span(span)))


def _list_node(elems: list[dict[str, Any]], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The dict-IR list node one element run and one list type record spell."""

    return node_to_dict(_ir.list_lit([dict_to_node(item) for item in elems], dict_to_type(typ), _mk_span(span)))


def _span_to(node: ast.AST, sub: ast.AST) -> ast.AST:
    """Copy a call's source span onto a synthesized expression, like the sorted block does."""

    for item in ast.walk(sub):
        item.lineno, item.col_offset = node.lineno, node.col_offset
        item.end_lineno, item.end_col_offset = node.end_lineno, node.end_col_offset
    return sub


def _pin(lo: Any, value: dict[str, Any], span: dict[str, Any], hint: str) -> dict[str, Any]:
    """Store a call argument in a fresh local so later uses read the name."""

    temp = lo._temp(hint)
    typ = _node_type(value)
    lo.locals[temp] = typ
    lo.prelude.append(_asg(temp, value, span))
    return _nm(temp, typ, span)


def _const_int(value: int, span: dict[str, Any]) -> dict[str, Any]:
    return _int(value, span)


def _const_text(value: str, kind: str, span: dict[str, Any]) -> dict[str, Any]:
    ptag = _PTAGS.get(kind, _ir.P_STR)
    return node_to_dict(_ir.const_node(ptag, value, _ir.t_leaf(kind), _mk_span(span)))


def _class(name: str) -> dict[str, Any]:
    return node_to_dict(_ir.t_class(name))


def _as_float(lo: Any, node: ast.AST, arg: dict[str, Any], span: dict[str, Any], what: str) -> dict[str, Any]:
    kind = _node_type_kind(arg)
    if kind == "int":
        return node_to_dict(_ir.cast("int_to_float", dict_to_node(arg), _ir.t_leaf("float"), _mk_span(span)))
    if kind != "float":
        lo.fail(node, "PS1003", f"{what} argument is {kind}, expected float")
    return arg


def _nm(name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(_ir.name_expr(name, dict_to_type(typ), _mk_span(span)))


def _int(value: int, span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(_ir.const_node(_ir.P_INT, str(value), _ir.t_leaf("int"), _mk_span(span)))


def _asg(target: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(_ir.assign(target, dict_to_node(value), _mk_span(span)))


def _cmp(op: str, left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(_ir.compare(op, dict_to_node(left), dict_to_node(right), _ir.t_leaf("bool"), _mk_span(span)))


def _binary(
    op: str, left: dict[str, Any], right: dict[str, Any], typ: dict[str, Any], span: dict[str, Any]
) -> dict[str, Any]:
    """The dict-IR binary node one operator, operand pair and result type spell."""

    return node_to_dict(_ir.binary(op, dict_to_node(left), dict_to_node(right), dict_to_type(typ), _mk_span(span)))


def _field(value: dict[str, Any], name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(_ir.attr(name, dict_to_type(typ), dict_to_node(value), _mk_span(span)))


def _sub(value: dict[str, Any], index: dict[str, Any], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(_ir.subscript(dict_to_node(value), dict_to_node(index), dict_to_type(typ), _mk_span(span)))


def _len_of(seq: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(
        _ir.call(
            _ir.builtin("len"),
            [dict_to_node(seq)],
            _ir.t_leaf("int"),
            _mk_span(span),
            "",
            "",
            -1,
        )
    )


def _set_field(obj: dict[str, Any], name: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(_ir.setattr_stmt(dict_to_node(obj), name, dict_to_node(value), _mk_span(span)))


def _incr(left: dict[str, Any], by: int, span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(
        _ir.binary("+", dict_to_node(left), dict_to_node(_int(by, span)), _ir.t_leaf("int"), _mk_span(span))
    )


def _tname(lo: Any, hint: str) -> str:
    """A fresh temp usable as a Python identifier in generated source."""
    return lo._temp(hint).replace(".", "_")


def _emit(lo: Any, node: ast.AST, lines: list[str], span: dict[str, Any]) -> None:
    """Lower generated Python source lines into the prelude, like the sorted
    block: statements re-parse and run through `_body`."""
    for stmt in ast.parse("\n".join(lines)).body:
        for sub in ast.walk(stmt):
            sub.lineno, sub.col_offset = node.lineno, node.col_offset
            sub.end_lineno, sub.end_col_offset = node.end_lineno, node.end_col_offset
        lo.prelude.extend(lo._body([stmt]))


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    from pyscriptc.ir import types_equal

    return types_equal(a, b)
