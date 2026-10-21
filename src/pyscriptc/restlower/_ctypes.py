"""ctypes.sizeof lowering."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.stdmore import SIZEOF


def _sizeof(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "ctypes.sizeof expects 1 argument(s)")
    name = _ctype(lo, node.args[0])
    if name is None:
        lo.fail(node, "PS2001", "ctypes.sizeof argument is not a lowered ctype")
    return {"kind": "const", "value": str(SIZEOF[name]), "type": {"kind": "int"}, "span": span}


def _ctype(lo: Any, node: ast.expr) -> str | None:
    if isinstance(node, ast.Attribute):
        resolved = lo._resolve_module(node)
        if resolved is not None and resolved[1] == "ctypes" and resolved[2] in SIZEOF:
            return str(resolved[2])
    if isinstance(node, ast.Name):
        binding = lo.bindings.get(node.id)
        if binding is not None and binding.module == "ctypes" and binding.attr in SIZEOF:
            return str(binding.attr)
    return None
