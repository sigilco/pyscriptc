"""pathlib and io buffer constructors."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import _class, _const_text


def _one(
    lo: Any,
    node: ast.Call,
    span: dict[str, Any],
    module: str,
    attr: str,
    symbol: str,
    want: dict[str, Any],
    ret: dict[str, Any],
) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", f"{module}.{attr} expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    lo._expect(node, arg["type"], want, f"{module}.{attr}")
    return lo._abi_call(module, attr, [arg], ret, symbol, span)


def _buffer(
    lo: Any, node: ast.Call, span: dict[str, Any], module: str, attr: str, symbol: str, kind: str, cls: str
) -> dict[str, Any]:
    if len(node.args) > 1:
        lo.fail(node, "PS2003", f"{module}.{attr} expects 0 or 1 argument(s)")
    if node.args:
        arg = lo._expr(node.args[0])
        if arg["type"]["kind"] != kind:
            lo.fail(node, "PS1003", f"{module}.{attr} argument is {kind}")
    else:
        arg = _const_text("", kind, span)
    return lo._abi_call(module, attr, [arg], _class(cls), symbol, span)
