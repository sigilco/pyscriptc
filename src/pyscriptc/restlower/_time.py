"""time module call lowering."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import _as_float, _class, _node_type, _t_leaf


def _sleep(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "time.sleep expects 1 argument(s)")
    arg = _as_float(lo, node, lo._expr(node.args[0]), span, "time.sleep")
    return lo._abi_call("time", "sleep", [arg], _t_leaf("none"), "ps_time_sleep", span)


def _clock(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    if len(node.args) > 1:
        lo.fail(node, "PS2003", f"time.{attr} expects 0 or 1 argument(s)")
    if node.args:
        arg = _as_float(lo, node, lo._expr(node.args[0]), span, f"time.{attr}")
    else:
        arg = lo._abi_call("time", "time", [], _t_leaf("float"), "ps_time_time", span)
    symbol = "ps_time_gm" if attr == "gmtime" else "ps_time_local"
    return lo._abi_call("time", attr, [arg], _class("StructTime"), symbol, span)


def _strftime(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    # `strftime(fmt)` is `strftime(fmt, localtime())` in CPython; the subset
    # keeps that default by lowering the clock call in its place.
    lo._reject_keywords(node)
    str_t = _t_leaf("str")
    if len(node.args) == 1:
        st = lo._abi_call("time", "localtime", [], _class("StructTime"), "ps_time_local", span)
        fmt = lo._expr(node.args[0], str_t)
        lo._expect(node.args[0], _node_type(fmt), str_t, "format")
        return lo._abi_call("time", "strftime", [fmt, st], str_t, "ps_time_strftime", span)
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "time.strftime expects 1 or 2 argument(s)")
    fmt = lo._expr(node.args[0], str_t)
    lo._expect(node.args[0], _node_type(fmt), str_t, "format")
    st = lo._expr(node.args[1])
    return lo._abi_call("time", "strftime", [fmt, st], str_t, "ps_time_strftime", span)
