"""jwt call lowering for the pyscriptc-jwt pack.

`jwt.encode(payload, key, algorithm="HS256")` and `jwt.decode(token, key,
algorithms)` lower onto the pack's C symbols (runtime/ps_jwt.c). The closed
subset is the PyJWT HMAC family: HS256, HS384 and HS512, a dict[str, str]
payload, and the claims carried as str. The keyword form `algorithm=` and the
positional form both take the same literal str; the exception constants
(`jwt.PyJWTError` and friends) stay fenced, as do every RS/ES algorithm,
`jwt.get_unverified_header` and the options dict. Errors raise ValueError
where PyJWT raises its own InvalidTokenError hierarchy.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.lower._core import _show
from pyscriptc.restlower._shared import _const_text

_STR: dict[str, Any] = {"kind": "str"}
_LIST_STR: dict[str, Any] = {"kind": "list", "elem": _STR}
_DICT_STR: dict[str, Any] = {"kind": "dict", "key": _STR, "value": _STR}


def _jwt_call(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None, attr: str
) -> dict[str, Any]:
    if attr == "encode":
        return _encode(lo, node, span)
    if attr == "decode":
        return _decode(lo, node, span, expect)
    lo.fail(node, "PS2001", f"jwt.{attr} is not in the lowered subset of jwt")
    raise AssertionError("unreachable")


def _dict_str(typ: dict[str, Any]) -> bool:
    return typ["kind"] == "dict" and typ["key"]["kind"] == "str" and typ["value"]["kind"] == "str"


def _buf_arg(lo: Any, node: ast.Call, expr: ast.expr, what: str) -> dict[str, Any]:
    arg = lo._expr(expr)
    if arg["type"]["kind"] not in {"str", "bytes"}:
        lo.fail(node, "PS1003", f"{what} is " + _show(arg["type"]) + ", expected str")
    return arg


def _encode(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) not in {2, 3}:
        lo.fail(node, "PS2003", "jwt.encode expects 2-3 argument(s)")
    alg_node = None
    for keyword in node.keywords:
        if keyword.arg != "algorithm":
            lo.fail(node, "PS2003", f"jwt.encode keyword {keyword.arg} is not lowered")
        alg_node = keyword.value
    if len(node.args) == 3:
        if alg_node is not None:
            lo.fail(node, "PS2003", "jwt.encode takes algorithm once")
        alg_node = node.args[2]
    if alg_node is None:
        alg_name = "HS256"
    elif isinstance(alg_node, ast.Constant) and isinstance(alg_node.value, str):
        alg_name = alg_node.value
    else:
        alg_name = None
    if alg_name not in {"HS256", "HS384", "HS512"}:
        lo.fail(node, "PS2001", "jwt.encode algorithm is a literal HS256, HS384, or HS512")
    alg = _const_text(alg_name, "str", span)
    payload = lo._expr(node.args[0])
    if not _dict_str(payload["type"]):
        lo.fail(node, "PS1003", "jwt.encode payload is " + _show(payload["type"]) + ", expected dict[str, str]")
    key = _buf_arg(lo, node, node.args[1], "jwt.encode key")
    args = [payload, key, alg]
    return lo._abi_call("jwt", "encode", args, {"kind": "str"}, "ps_jwt_encode", span)


def _decode(lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
    lo._reject_keywords(node)
    if len(node.args) != 3:
        lo.fail(node, "PS2003", "jwt.decode expects 3 argument(s)")
    if expect is None:
        lo.fail(node, "PS1001", "jwt.decode needs the result annotation")
    if not _dict_str(expect):
        lo.fail(node, "PS1003", "jwt.decode returns dict[str, str], got " + _show(expect))
    token = _buf_arg(lo, node, node.args[0], "jwt.decode token")
    key = _buf_arg(lo, node, node.args[1], "jwt.decode key")
    algorithms = lo._expr(node.args[2])
    if not (
        algorithms["type"]["kind"] == "list"
        and algorithms["type"].get("elem", {}).get("kind") == "str"
        and not algorithms["type"]["elem"].get("optional")
    ):
        lo.fail(node, "PS1003", "jwt.decode algorithms is " + _show(algorithms["type"]) + ", expected list[str]")
    args = [token, key, algorithms]
    return lo._abi_call("jwt", "decode", args, expect, "ps_jwt_decode", span)
