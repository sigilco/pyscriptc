"""uuid call lowering.

The closed subset is `uuid4`, `uuid1`, `uuid5` and the `UUID` class as the
canonical text. The runtime (`ps_uuid.c`) draws the random bytes, sets the
version and variant bits, and prints the canonical form, the way CPython's
`uuid` module does.

`UUID` is not lowered as a class: `UUID(text)` returns the canonical text, so
`str(UUID(text))` and the `f"{UUID(text)}"` form read the same as CPython's.
The `.hex` attribute of a lowered uuid call (`uuid4().hex`) is the canonical
text without the dashes, which is what CPython's `.hex` holds; it lowers to
`ps_uuid_hex` on the same text. The `.int`, `.urn` and `.bytes` forms stay
refused.
"""

from __future__ import annotations

import ast
from typing import Any


def uuid_hex_attr(lo: Any, obj: dict[str, Any], name: str, span: dict[str, Any]) -> dict[str, Any] | None:
    """`.hex` on a lowered uuid call result, or None for another attribute.

    The lowerer's attribute walk calls this before the plain-str refusal, so
    `uuid4().hex` reads the way CPython's does without claiming `str` has a
    `.hex`: the receiver must be a uuid call expression.
    """
    if name != "hex":
        return None
    callee = obj.get("callee") or {}
    if callee.get("module") != "uuid" or callee.get("name") not in {"uuid1", "uuid4", "uuid5", "UUID"}:
        return None
    return lo._abi_call("uuid", "hex", [obj], {"kind": "str"}, "ps_uuid_hex", span)


def _text_arg(lo: Any, node: ast.AST, value: dict[str, Any], what: str) -> dict[str, Any]:
    if value["type"]["kind"] != "str":
        lo.fail(node, "PS1003", f"{what} argument is str")
    return value


def _uuid_call(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """The uuid calls: `uuid4` and `uuid1` take no argument, `uuid5` takes the
    namespace and the name."""
    lo._reject_keywords(node)
    if attr in {"uuid4", "uuid1"}:
        if node.args:
            lo.fail(node, "PS2003", f"uuid.{attr} expects 0 argument(s)")
        symbol = "ps_uuid4" if attr == "uuid4" else "ps_uuid1"
        return lo._abi_call("uuid", attr, [], {"kind": "str"}, symbol, span)
    if attr == "uuid5":
        if len(node.args) != 2:
            lo.fail(node, "PS2003", "uuid.uuid5 expects 2 argument(s)")
        namespace = _text_arg(lo, node, lo._expr(node.args[0]), "uuid.uuid5 namespace")
        name = _text_arg(lo, node, lo._expr(node.args[1]), "uuid.uuid5 name")
        return lo._abi_call("uuid", attr, [namespace, name], {"kind": "str"}, "ps_uuid5", span)
    if attr == "UUID":
        return _uuid_construct(lo, node, span)
    lo.fail(node, "PS2001", f"uuid.{attr} is not in the lowered subset of uuid")
    raise AssertionError("unreachable")


def _uuid_construct(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`UUID(text)`: the canonical text of the parsed UUID.

    The class is not a native class: the call returns the canonical form, the
    way `str(UUID(text))` reads in CPython.
    """
    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "uuid.UUID expects 1 argument(s)")
    text = _text_arg(lo, node, lo._expr(node.args[0]), "uuid.UUID")
    return lo._abi_call("uuid", "UUID", [text], {"kind": "str"}, "ps_uuid_parse", span)
