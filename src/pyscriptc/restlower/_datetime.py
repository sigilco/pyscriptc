"""datetime constructor lowering."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import _class, _const_int


def _ints(
    lo: Any,
    node: ast.Call,
    span: dict[str, Any],
    module: str,
    attr: str,
    symbol: str,
    cls: str,
    minimum: int,
    names: list[str],
) -> dict[str, Any]:
    params: list[dict[str, Any]] = []
    for index, name in enumerate(names):
        param: dict[str, Any] = {"name": name, "type": {"kind": "int"}}
        if index >= minimum:
            param["default"] = _const_int(0, span)
        params.append(param)
    args = lo._fill_args(node, params, f"{module}.{attr}")
    return lo._abi_call(module, attr, args, _class(cls), symbol, span)
