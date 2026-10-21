"""hmac call lowering."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import _const_int

_HMAC_ALGS = {"md5": 1, "sha1": 2, "sha256": 3, "sha384": 4, "sha512": 5}


def _hmac_alg(lo: Any, node: ast.expr) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _HMAC_ALGS.get(node.value.lower())
    member = ""
    if isinstance(node, ast.Attribute):
        resolved = lo._resolve_module(node)
        if resolved is not None and resolved[1] == "hashlib":
            member = resolved[2]
    elif isinstance(node, ast.Name):
        binding = lo.bindings.get(node.id)
        if binding is not None and binding.kind == "func" and binding.module == "hashlib":
            member = binding.attr
    if not member:
        return None
    return _HMAC_ALGS.get(member)


def _hmac(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    if len(node.args) != 3:
        lo.fail(node, "PS2003", f"hmac.{attr} expects 3 argument(s)")
    key = lo._expr(node.args[0])
    msg = lo._expr(node.args[1])
    if key["type"]["kind"] != "bytes" or msg["type"]["kind"] != "bytes":
        lo.fail(node, "PS1003", f"hmac.{attr} key and message are bytes")
    alg = _hmac_alg(lo, node.args[2])
    if alg is None:
        lo.fail(node, "PS2001", "hmac algorithm is a literal md5, sha1, sha256, sha384, or sha512")
    alg_expr = _const_int(alg, span)
    if attr == "digest":
        return lo._abi_call("hmac", "digest", [key, msg, alg_expr], {"kind": "bytes"}, "ps_hmac_digest", span)
    return lo._abi_call("hmac", "new", [key, msg, alg_expr], {"kind": "class", "name": "Hmac"}, "ps_hmac_new", span)


def _hmac_compare(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "hmac.compare_digest expects 2 argument(s)")
    left = lo._expr(node.args[0])
    right = lo._expr(node.args[1])
    kind = left["type"]["kind"]
    if kind not in {"str", "bytes"} or right["type"]["kind"] != kind:
        lo.fail(node, "PS1003", "hmac.compare_digest takes two str or two bytes")
    return lo._abi_call("hmac", "compare_digest", [left, right], {"kind": "bool"}, "ps_hmac_compare", span)
