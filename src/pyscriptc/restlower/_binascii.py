"""binascii call lowering.

The closed subset is `unhexlify` and `hexlify`. The runtime
(`ps_binascii.c`) decodes the hexadecimal text into bytes and encodes bytes
back into the lowercase hexadecimal text, the way CPython's `binascii` does.

`unhexlify` takes a str (the hex text) and returns bytes. `hexlify` takes
bytes and returns bytes, which is what CPython returns; `hexlify` is a
bytes-to-bytes codec there, not a bytes-to-str one.
"""

from __future__ import annotations

import ast
from typing import Any


def _binascii_call(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """`binascii.unhexlify(text)` and `binascii.hexlify(data)`."""
    lo._reject_keywords(node)
    if attr == "unhexlify":
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "binascii.unhexlify expects 1 argument(s)")
        arg = lo._expr(node.args[0])
        if arg["type"]["kind"] not in {"str", "bytes"}:
            lo.fail(node, "PS1003", "binascii.unhexlify argument is str")
        return lo._abi_call("binascii", "unhexlify", [arg], {"kind": "bytes"}, "ps_bin_unhexlify", span)
    if attr == "hexlify":
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "binascii.hexlify expects 1 argument(s)")
        arg = lo._expr(node.args[0])
        if arg["type"]["kind"] != "bytes":
            lo.fail(node, "PS1003", "binascii.hexlify argument is bytes")
        return lo._abi_call("binascii", "hexlify", [arg], {"kind": "bytes"}, "ps_bin_hexlify", span)
    lo.fail(node, "PS2001", f"binascii.{attr} is not in the lowered subset of binascii")
    raise AssertionError("unreachable")
