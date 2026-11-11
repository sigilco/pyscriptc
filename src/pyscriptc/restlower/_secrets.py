"""secrets call lowering.

The closed subset is `token_hex`, `token_bytes` and `randbelow`. The runtime
(`ps_secrets.c`) draws the bytes from the host entropy source, the way
CPython's `secrets` does through `SystemRandom`.

`token_hex(nbytes)` returns the lowercase hexadecimal text of `nbytes`
random bytes, `token_bytes(nbytes)` the bytes, and `randbelow(n)` an int in
`[0, n)`.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import _const_int


def _count_arg(
    lo: Any, node: ast.Call, slots: list[ast.expr | None], what: str, span: dict[str, Any]
) -> dict[str, Any]:
    """The byte count: a constant int, or the CPython default of 32."""
    if slots[0] is None:
        return _const_int(32, span)
    arg = lo._expr(slots[0])
    if arg["kind"] == "const" and arg["type"]["kind"] == "int":
        return arg
    lo.fail(node, "PS1003", f"{what} count is a constant int")
    raise AssertionError("unreachable")


def _secrets_call(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """`secrets.token_hex(n)`, `secrets.token_bytes(n)` and `secrets.randbelow(n)`."""
    if attr in {"token_hex", "token_bytes"}:
        slots = lo._place_nodes(node, ["nbytes"])
        count = _count_arg(lo, node, slots, f"secrets.{attr}", span)
        kind = "str" if attr == "token_hex" else "bytes"
        return lo._abi_call("secrets", attr, [count], {"kind": kind}, f"ps_secrets_{attr}", span)
    if attr == "randbelow":
        slots = lo._place_nodes(node, ["exclusive_upper_bound"])
        if slots[0] is None:
            lo.fail(node, "PS2003", "secrets.randbelow expects a bound argument")
        assert slots[0] is not None
        bound = lo._expr(slots[0])
        if bound["type"]["kind"] != "int":
            lo.fail(node, "PS1003", "secrets.randbelow bound is int")
        return lo._abi_call("secrets", "randbelow", [bound], {"kind": "int"}, "ps_secrets_randbelow", span)
    lo.fail(node, "PS2001", f"secrets.{attr} is not in the lowered subset of secrets")
    raise AssertionError("unreachable")
