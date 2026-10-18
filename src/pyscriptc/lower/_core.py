"""Leaf definitions shared by every module in the `lower` package.

`Fence`, `Binding`, and the small pure helpers live here so sibling modules
(`_stdlib.py`, `__init__.py`) can import them without a cycle. Moved verbatim
from `lower.py`; no signature or behavior change.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.ir import types_equal


class Fence(Exception):
    def __init__(self, code: str, message: str, span: dict[str, Any], function: str | None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.span = span
        self.function = function

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "function": self.function,
            "span": self.span,
        }


class Binding:
    def __init__(
        self,
        kind: str,
        module: str,
        attr: str,
        queued: bool,
        typ: dict[str, Any] | None = None,
        rooted: bool = False,
    ) -> None:
        self.kind = kind
        self.module = module
        self.attr = attr
        self.queued = queued
        self.type = typ
        # True when `import urllib.parse` binds `urllib`, not the whole module.
        self.rooted = rooted


def _assign(target: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "assign", "target": target, "value": value, "span": span}


def _const_str_join(parts: list[dict[str, Any]], span: dict[str, Any]) -> dict[str, Any] | None:
    """Module-level f-string pieces that are already str constants are one str constant."""

    if any(part.get("kind") != "const" or part.get("type", {}).get("kind") != "str" for part in parts):
        return None
    text = "".join(str(part["value"]) for part in parts)
    return {"kind": "const", "value": text, "type": {"kind": "str"}, "span": span}


def _concat_str(parts: list[dict[str, Any]], span: dict[str, Any]) -> dict[str, Any]:
    if not parts:
        return {"kind": "const", "value": "", "type": {"kind": "str"}, "span": span}
    acc = parts[0]
    for part in parts[1:]:
        acc = {"kind": "binary", "op": "+", "left": acc, "right": part, "type": {"kind": "str"}, "span": span}
    return acc


def _percent_pieces(fmt: str) -> list[tuple[str, str]] | None:
    pieces: list[tuple[str, str]] = []
    buf: list[str] = []
    index = 0
    size = len(fmt)
    while index < size:
        if fmt[index] != "%":
            buf.append(fmt[index])
            index += 1
            continue
        if index + 1 >= size:
            return None
        nxt = fmt[index + 1]
        if nxt == "%":
            buf.append("%")
            index += 2
            continue
        if nxt not in {"s", "d"}:
            return None
        pieces.append(("lit", "".join(buf)))
        buf = []
        pieces.append((nxt, ""))
        index += 2
    pieces.append(("lit", "".join(buf)))
    return pieces


def _const_list_concat(left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]) -> dict[str, Any] | None:
    """Two constant lists of one element type are one list. A runtime list stays fenced."""

    if left.get("kind") != "list" or right.get("kind") != "list":
        return None
    if left["type"]["kind"] != "list" or right["type"]["kind"] != "list":
        return None
    if left["type"].get("set") or right["type"].get("set"):
        return None
    if not types_equal(left["type"]["elem"], right["type"]["elem"]):
        return None
    return {
        "kind": "list",
        "elems": [*left["elems"], *right["elems"]],
        "type": {"kind": "list", "elem": left["type"]["elem"]},
        "span": span,
    }


def _name(name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "name", "id": name, "type": typ, "span": span}


def _const_int(value: int, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": str(value), "type": {"kind": "int"}, "span": span}


def _int_text(value: str) -> bool:
    body = value[1:] if value.startswith("-") else value
    return bool(body) and body.isdigit()


def _static_int(expr: dict[str, Any]) -> int | None:
    if expr.get("kind") == "const" and expr.get("type", {}).get("kind") == "int":
        value = expr.get("value")
        if isinstance(value, str) and _int_text(value):
            return int(value)
    if expr.get("kind") == "unary" and expr.get("op") == "-" and isinstance(expr.get("operand"), dict):
        inner = _static_int(expr["operand"])
        if inner is not None:
            return -inner
    return None


def _const_bool(value: bool, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": value, "type": {"kind": "bool"}, "span": span}


def _const_none(span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": None, "type": {"kind": "none"}, "span": span}


def _const_truthy(value: dict[str, Any] | None) -> bool | None:
    """Python truthiness of a folded constant. None means not decidable."""
    if value is None:
        return None
    kind = value["kind"]
    if kind == "const":
        typ = value["type"]["kind"]
        if typ == "bool":
            return bool(value["value"])
        if typ == "int":
            return int(value["value"]) != 0
        if typ == "float":
            return float(value["value"]) != 0.0
        if typ in {"str", "bytes"}:
            return len(value["value"]) > 0
        if typ == "none":
            return False
        return None
    if kind == "list":
        return len(value["elems"]) > 0
    if kind == "dict":
        return len(value["pairs"]) > 0
    return None


def _module_closed(value: dict[str, Any]) -> bool:
    """A storable module constant: no `name` references anywhere inside.

    A `name` points at a prelude temp that only exists inside a function
    body, so storing one at module scope dangles. Every other kind re-emits
    at each read, which is what stored calls and literals already do.
    """
    kind = value["kind"]
    if kind == "name":
        return False
    if kind == "list":
        return all(_module_closed(elem) for elem in value["elems"])
    if kind == "dict":
        return all(_module_closed(pair["key"]) and _module_closed(pair["value"]) for pair in value["pairs"])
    return True


def _len_call(seq: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "call",
        "callee": {"kind": "builtin", "name": "len"},
        "args": [seq],
        "type": {"kind": "int"},
        "span": span,
    }


def _binary_int(op: str, left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "binary", "op": op, "left": left, "right": right, "type": {"kind": "int"}, "span": span}


def _compare(op: str, left: dict[str, Any], right: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "compare", "op": op, "left": left, "right": right, "type": {"kind": "bool"}, "span": span}


def _show(typ: dict[str, Any]) -> str:
    if typ["kind"] == "list":
        head = "set" if typ.get("set") else "list"
        return f"{head}[{_show(typ['elem'])}]"
    if typ["kind"] == "dict":
        return f"dict[{_show(typ['key'])}, {_show(typ['value'])}]"
    if typ["kind"] == "class":
        return str(typ.get("label", typ["name"]))
    return str(typ["kind"])


def _plain_const_kind(node: ast.expr) -> str | None:
    """A str constant or an int constant. Bool is not an int here."""
    if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
        return None
    if isinstance(node.value, str):
        return "str"
    if isinstance(node.value, int):
        return "int"
    return None


def _dotted(node: ast.Attribute) -> str:
    parts: list[str] = [node.attr]
    cur: ast.AST = node.value
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    parts.reverse()
    return ".".join(parts)


_CMP = {
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Eq: "==",
    ast.NotEq: "!=",
}
_SCALAR = {"int", "float", "str", "bytes"}
_TRUTHY = frozenset({"str", "bytes", "int", "list", "dict"})
_LIST_FORMS = frozenset({"List"})
_DICT_FORMS = frozenset({"Dict"})
_SET_FORMS = frozenset({"Set"})


_FUTURE_OK = frozenset(
    {
        "annotations",
        "generator_stop",
        "nested_scopes",
        "generators",
        "division",
        "absolute_import",
        "with_statement",
        "print_function",
        "unicode_literals",
    }
)


def _package_base(module_name: str, is_package: bool, level: int) -> list[str] | None:
    if level < 1 or not module_name:
        return None
    bits = module_name.split(".")
    if not is_package:
        bits = bits[:-1]
    drop = level - 1
    if drop > len(bits):
        return None
    return bits[: len(bits) - drop]


def _tuple_assign(
    dest: str, typ: dict[str, Any], elems: list[dict[str, Any]], span: dict[str, Any]
) -> list[dict[str, Any]]:
    made = {
        "kind": "call",
        "callee": {"kind": "class", "name": typ["name"]},
        "args": [],
        "type": typ,
        "span": span,
    }
    stmts = [_assign(dest, made, span)]
    for index, elem in enumerate(elems):
        stmts.append(
            {
                "kind": "setattr",
                "object": _name(dest, typ, span),
                "name": f"_{index}",
                "value": elem,
                "span": span,
            }
        )
    return stmts


def _zero(typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    if typ["kind"] == "int":
        return _const_int(0, span)
    if typ["kind"] == "bool":
        return _const_bool(False, span)
    if typ["kind"] == "float":
        return {"kind": "const", "value": "0.0", "type": typ, "span": span}
    if typ["kind"] == "list":
        return {"kind": "list", "elems": [], "type": typ, "span": span}
    if typ["kind"] == "class":
        # Fresh for-targets of a synthetic tuple class start as a zeroed
        # instance (ps_alloc calloc); the loop step then rebinding every field.
        return {
            "kind": "call",
            "callee": {"kind": "class", "name": typ["name"]},
            "args": [],
            "type": typ,
            "span": span,
        }
    return {"kind": "const", "value": "", "type": typ, "span": span}


def _attr(value: dict[str, Any], name: str, typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "attr", "value": value, "name": name, "type": typ, "span": span}


def _item(value: dict[str, Any], index: dict[str, Any], typ: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "subscript", "value": value, "index": index, "type": typ, "span": span}


def _const_str(value: str, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": value, "type": {"kind": "str"}, "span": span}


def _raise_value(message: dict[str, Any] | None, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "raise", "exc": "ValueError", "message": message, "span": span}


def _is_none_expr(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _declared_globals(body: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Global):
                names.update(node.names)
    return names
