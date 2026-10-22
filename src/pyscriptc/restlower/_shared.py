"""Leaf helpers shared by the restlower call handlers."""

from __future__ import annotations

import ast
from typing import Any

_CMP = {"eq": ast.Eq, "ne": ast.NotEq, "lt": ast.Lt, "le": ast.LtE, "gt": ast.Gt, "ge": ast.GtE}


_BIN = {"add": ast.Add, "sub": ast.Sub, "mul": ast.Mult, "floordiv": ast.FloorDiv, "truediv": ast.Div, "mod": ast.Mod}


def _span_to(node: ast.AST, sub: ast.AST) -> ast.AST:
    """Copy a call's source span onto a synthesized expression, like the sorted block does."""

    for item in ast.walk(sub):
        item.lineno, item.col_offset = node.lineno, node.col_offset
        item.end_lineno, item.end_col_offset = node.end_lineno, node.end_col_offset
    return sub


def _pin(lo: Any, value: dict[str, Any], span: dict[str, Any], hint: str) -> dict[str, Any]:
    """Store a call argument in a fresh local so later uses read the name."""

    temp = lo._temp(hint)
    lo.locals[temp] = value["type"]
    lo.prelude.append({"kind": "assign", "target": temp, "value": value, "span": span})
    return {"kind": "name", "id": temp, "type": value["type"], "span": span}


def _const_int(value: int, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": str(value), "type": {"kind": "int"}, "span": span}


def _const_text(value: str, kind: str, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": value, "type": {"kind": kind}, "span": span}


def _class(name: str) -> dict[str, Any]:
    return {"kind": "class", "name": name}


def _as_float(lo: Any, node: ast.AST, arg: dict[str, Any], span: dict[str, Any], what: str) -> dict[str, Any]:
    if arg["type"]["kind"] == "int":
        return {"kind": "cast", "op": "int_to_float", "value": arg, "type": {"kind": "float"}, "span": span}
    if arg["type"]["kind"] != "float":
        lo.fail(node, "PS1003", f"{what} argument is {arg['type']['kind']}, expected float")
    return arg


def _nm(name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "name", "id": name, "type": typ, "span": span}


def _int(value: int, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": str(value), "type": {"kind": "int"}, "span": span}


def _asg(target: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "assign", "target": target, "value": value, "span": span}


def _cmp(op: str, left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "compare", "op": op, "left": left, "right": right, "type": {"kind": "bool"}, "span": span}


def _field(value: dict[str, Any], name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "attr", "value": value, "name": name, "type": typ, "span": span}


def _sub(value: dict[str, Any], index: dict[str, Any], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "subscript", "value": value, "index": index, "type": typ, "span": span}


def _len_of(seq: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "call",
        "callee": {"kind": "builtin", "name": "len"},
        "args": [seq],
        "type": {"kind": "int"},
        "span": span,
    }


def _set_field(obj: dict[str, Any], name: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "setattr", "object": obj, "name": name, "value": value, "span": span}


def _incr(left: dict[str, Any], by: int, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "binary", "op": "+", "left": left, "right": _int(by, span), "type": {"kind": "int"}, "span": span}


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
