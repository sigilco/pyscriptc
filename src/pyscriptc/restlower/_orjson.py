"""orjson call lowering for the pyscriptc-orjson pack.

`orjson.dumps(obj, option=...)` returns bytes: the pack writes the compact
JSON through the runtime's json writer and hands the buffer back as bytes,
with the option subset `orjson.OPT_SORT_KEYS` and `orjson.OPT_INDENT_2`
mapped onto the writer's flags. `orjson.loads(data)` reads a str or bytes
into the annotated type, so the shape rules are the json.loads ones.
Everything else in orjson (the `default` callable, the `OPT_*` flags outside
the subset, `orjson.Fragment`, `orjson.JSONDecodeError`) stays fenced.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.lower._stdlib_shared import _mod_call

_BYTES: dict[str, Any] = {"kind": "bytes"}

# The option subset. The writer's flag word is a different layout (bit 0
# sort keys, bits 8-15 indent, bit 16 compact), so `_option` maps the orjson
# names onto it.


def _orjson_call(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None, attr: str
) -> dict[str, Any]:
    if attr == "dumps":
        return _dumps(lo, node, span)
    if attr == "loads":
        return _loads(lo, node, span, expect)
    lo.fail(node, "PS2001", f"orjson.{attr} is not in the lowered subset of orjson")
    raise AssertionError("unreachable")


def _dumps(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "orjson.dumps expects 1 argument(s)")
    flags = 0x10000 | 2  # compact, and non-ASCII passes through raw (orjson never escapes)
    for keyword in node.keywords:
        if keyword.arg == "option":
            flags |= _option(lo, node, keyword.value)
        elif keyword.arg == "default":
            lo.fail(node, "PS2003", "orjson.dumps default is not lowered")
        else:
            lo.fail(node, "PS2003", f"orjson.dumps keyword {keyword.arg} is not lowered")
    value = lo._expr(node.args[0])
    lo._json_type(node, value["type"], "orjson.dumps")
    expr = _mod_call("orjson", "dumps", [value], _BYTES, span)
    expr["json_flags"] = flags
    return expr


def _option(lo: Any, node: ast.Call, expr: ast.expr) -> int:
    """The `option=` argument: one or more orjson.OPT_* names, or-ed."""
    if isinstance(expr, ast.Attribute):
        if expr.attr == "OPT_SORT_KEYS":
            return 1
        if expr.attr == "OPT_INDENT_2":
            return 2 << 8
        lo.fail(node, "PS2003", f"orjson.dumps option {expr.attr} is not in the lowered subset")
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
        return _option(lo, node, expr.left) | _option(lo, node, expr.right)
    lo.fail(node, "PS2003", "orjson.dumps option is an orjson.OPT_* name")
    raise AssertionError("unreachable")


def _loads(lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "orjson.loads expects 1 argument(s)")
    lo._reject_keywords(node)
    if expect is None:
        lo.fail(node, "PS1001", "orjson.loads needs the result annotation")
    lo._json_type(node, expect, "orjson.loads")
    arg = lo._expr(node.args[0])
    if arg["type"]["kind"] not in {"str", "bytes"}:
        lo.fail(node, "PS1003", "orjson.loads argument is " + repr(arg["type"]) + ", expected str or bytes")
    return _mod_call("orjson", "loads", [arg], expect, span)
