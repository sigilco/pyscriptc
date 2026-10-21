"""copy call lowering."""

from __future__ import annotations

import ast
from typing import Any

_COPY_SCALARS = {"int", "float", "bool", "str", "bytes"}


def _copyable(typ: dict[str, Any]) -> bool:
    kind = typ["kind"]
    if kind in {"int", "float", "str", "bytes"}:
        return True
    if kind == "list":
        return _copyable(typ["elem"])
    if kind == "dict":
        return _copyable(typ["value"])
    return False


def _copy_call(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """copy.copy/deepcopy: scalars are returned as-is; shallow copies of
    list/dict memcpy the container; deepcopy walks a static type table the
    emitter writes next to the call (ps_copy_deep)."""
    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", f"copy.{attr} expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    typ = arg["type"]
    kind = typ["kind"]
    if kind in _COPY_SCALARS:
        return arg
    if attr == "copy":
        if kind == "list":
            return lo._abi_call("copy", attr, [arg], typ, "ps_copy_seq", span)
        if kind == "dict":
            return lo._abi_call("copy", attr, [arg], typ, "ps_copy_map", span)
    else:
        if kind in {"list", "dict"} and _copyable(typ):
            return lo._abi_call("copy", attr, [arg], typ, "ps_copy_deep", span)
    lo.fail(node, "PS2001", f"copy.{attr} is not lowered for {kind}")
    raise AssertionError("unreachable")
