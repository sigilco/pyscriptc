"""hashlib call lowering: the one-shot form and the streaming hash object.

The closed subset is `md5`, `sha1`, `sha256`, `sha384` and `sha512`. The
one-shot form (`hashlib.sha256(data)`) hashes the whole buffer in one call
and returns the digest; the streaming form (`hashlib.sha256()`) opens a hash
object whose `update(data)` folds bytes in and whose `digest()` and
`hexdigest()` read the result (issue #1248).

Both forms return the same runtime record: a pointer to a `PsHash` context
(`ps_hash.c`). The one-shot call folds its argument into the context and
marks it done, so `hashlib.sha256(data).hexdigest()` reads the same digest
the one-shot call has always produced.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc import _irnode as _ir
from pyscriptc.lower._core import _assign, _mk_span, _name
from pyscriptc.restlower._shared import _class, _const_int, _node_type_kind, _t_leaf
from pyscriptc.tirserde import dict_to_node, node_to_dict

# The algorithm ids the runtime numbers (the hmac numbering: 1 md5, 2 sha1,
# 3 sha256, 4 sha384, 5 sha512).
_ALGS: dict[str, int] = {"md5": 1, "sha1": 2, "sha256": 3, "sha384": 4, "sha512": 5}


def _hashlib_call(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """`hashlib.<alg>(data)` and `hashlib.<alg>()`.

    The zero-argument form opens the streaming hash object; the one-argument
    form hashes the buffer in one call. Both return the same record, so the
    `hexdigest()` and `digest()` rows read it the same way.
    """
    lo._reject_keywords(node)
    alg = _ALGS.get(attr)
    if alg is None:
        lo.fail(node, "PS2001", f"hashlib.{attr} is not in the lowered subset of hashlib")
    if len(node.args) > 1:
        lo.fail(node, "PS2003", f"hashlib.{attr} expects 0 or 1 argument(s)")
    if not node.args:
        return lo._abi_call("hashlib", attr, [_const_int(alg, span)], _class("Hash"), "ps_hash_new", span)
    data = lo._expr(node.args[0])
    if _node_type_kind(data) not in {"str", "bytes"}:
        lo.fail(node, "PS1003", f"hashlib.{attr} argument is str or bytes")
    # The one-shot argument folds into the same context the constructor
    # makes. The context and the data each bind to a prelude temp so the
    # expression value stays a name: the emitted order is the constructor,
    # the fold, then the statement's own read of the record.
    h_t = lo._temp("hash").replace(".", "_")
    d_t = lo._temp("hashd").replace(".", "_")
    lo.locals[h_t] = _class("Hash")
    lo.locals[d_t] = data["type"]
    lo.prelude.append(
        _assign(h_t, lo._abi_call("hashlib", attr, [_const_int(alg, span)], _class("Hash"), "ps_hash_new", span), span)
    )
    lo.prelude.append(_assign(d_t, data, span))
    update = lo._abi_call(
        "hashlib",
        "Hash.update",
        [_name(h_t, _class("Hash"), span), _name(d_t, data["type"], span)],
        _t_leaf("none"),
        "ps_hash_update",
        span,
    )
    lo.prelude.append(node_to_dict(_ir.expr_stmt(dict_to_node(update), _mk_span(span))))
    return _name(h_t, _class("Hash"), span)


def _hashlib_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`h.update(data)`, `h.digest()` and `h.hexdigest()`.

    `update` returns None, like CPython. `digest` and `hexdigest` read the
    finalized digest; the runtime freezes it on the first read, so a second
    read returns the same bytes.
    """
    assert isinstance(node.func, ast.Attribute)
    lo._reject_keywords(node)
    attr = node.func.attr
    if attr == "update":
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "Hash.update expects 1 argument(s)")
        data = lo._expr(node.args[0])
        if _node_type_kind(data) not in {"str", "bytes"}:
            lo.fail(node, "PS1003", "Hash.update argument is str or bytes")
        return lo._abi_call("hashlib", "Hash.update", [receiver, data], _t_leaf("none"), "ps_hash_update", span)
    if attr == "digest":
        if node.args:
            lo.fail(node, "PS2003", "Hash.digest expects 0 argument(s)")
        return lo._abi_call("hashlib", "Hash.digest", [receiver], _t_leaf("bytes"), "ps_hash_digest_ctx", span)
    if attr == "hexdigest":
        if node.args:
            lo.fail(node, "PS2003", "Hash.hexdigest expects 0 argument(s)")
        return lo._abi_call("hashlib", "Hash.hexdigest", [receiver], _t_leaf("str"), "ps_hash_hexdigest", span)
    lo.fail(node, "PS2001", f"Hash.{attr} is not lowered")
    raise AssertionError("unreachable")


def _hashlib_alg(lo: Any, node: ast.expr) -> int | None:
    """The algorithm id of a `hashlib.<alg>` spelling, for the hmac rows."""
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
    return _ALGS.get(member)


__all__ = ["_hashlib_alg", "_hashlib_call", "_hashlib_method"]
