"""time module call lowering."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import _as_float, _class


def _sleep(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "time.sleep expects 1 argument(s)")
    arg = _as_float(lo, node, lo._expr(node.args[0]), span, "time.sleep")
    return lo._abi_call("time", "sleep", [arg], {"kind": "none"}, "ps_time_sleep", span)


def _clock(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    if len(node.args) > 1:
        lo.fail(node, "PS2003", f"time.{attr} expects 0 or 1 argument(s)")
    if node.args:
        arg = _as_float(lo, node, lo._expr(node.args[0]), span, f"time.{attr}")
    else:
        arg = lo._abi_call("time", "time", [], {"kind": "float"}, "ps_time_time", span)
    symbol = "ps_time_gm" if attr == "gmtime" else "ps_time_local"
    return lo._abi_call("time", attr, [arg], _class("StructTime"), symbol, span)
