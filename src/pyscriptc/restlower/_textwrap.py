"""textwrap call lowering."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import _const_int


def _textwrap_call(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """textwrap.fill/wrap/shorten take text plus an optional width (default 70);
    dedent and indent ride the fixed table."""
    if attr in {"fill", "wrap", "shorten"}:
        slots = lo._place_nodes(node, ["text", "width"])
        if slots[0] is None:
            lo.fail(node, "PS2003", f"textwrap.{attr} expects a text argument")
        assert slots[0] is not None
        text = lo._expr(slots[0])
        if text["type"]["kind"] != "str":
            lo.fail(node, "PS1003", f"textwrap.{attr} text is str")
        if slots[1] is None:
            if attr == "shorten":
                lo.fail(node, "PS2003", "textwrap.shorten expects a width argument")
            width = _const_int(70, span)
        else:
            width = lo._expr(slots[1])
            if width["type"]["kind"] != "int":
                lo.fail(node, "PS1003", f"textwrap.{attr} width is int")
        if attr == "wrap":
            return lo._abi_call(
                "textwrap",
                attr,
                [text, width],
                {"kind": "list", "elem": {"kind": "str"}},
                "ps_wrap_wrap",
                span,
            )
        symbol = "ps_wrap_fill" if attr == "fill" else "ps_wrap_shorten"
        return lo._abi_call("textwrap", attr, [text, width], {"kind": "str"}, symbol, span)
    lo.fail(node, "PS2001", f"textwrap.{attr} is not in the lowered subset of textwrap")
    raise AssertionError("unreachable")
