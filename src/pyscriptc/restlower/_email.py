"""email call lowering.

The closed subset is the MIME read surface: `message_from_string`,
`message_from_bytes` and `message_from_file` build an `EmailMessage`
value, and the message answers its headers (`get`, `get_all`, `keys`,
`items`), its structure (`is_multipart`, `walk`) and its payload
(`get_payload`, `get_content_type`, `get_filename`).

The runtime (`ps_email.c`) parses the message the way CPython's
compat32 policy does: the header block, the folded continuation lines,
the RFC 2047 encoded words in the header values, the RFC 2046 multipart
split on the boundary and the quoted-printable and base64 transfer
encodings in the payload.

Everything else in the `email` package (the `mime` builders, the `policy`
API, `EmailMessage` construction, `sendmail`, the header registry) is
refused with `PS2001`: the subset reads a message, it does not build one.
"""

# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import _node_type, _node_type_kind, _t_leaf, _t_list

_STR = {"kind": "str"}
_LIST_STR = {"kind": "list", "elem": {"kind": "str"}}
_MESSAGE = {"kind": "class", "name": "EmailMessage"}


def _email_call(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """`email.message_from_string(text)`, `message_from_bytes(data)` and
    `message_from_file(fp)`."""
    if attr not in {"message_from_string", "message_from_bytes", "message_from_file"}:
        lo.fail(node, "PS2001", f"email.{attr} is not in the lowered subset of email")
    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", f"email.{attr} expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    kind = _node_type_kind(arg)
    if attr == "message_from_file":
        typ = _node_type(arg)
        if typ["kind"] != "class" or typ.get("name") != "TextIO":
            lo.fail(node, "PS1003", "email.message_from_file takes a text file")
        return lo._abi_call("email", attr, [arg], _MESSAGE, "ps_email_from_file", span)
    if attr == "message_from_bytes":
        if kind != "bytes":
            lo.fail(node, "PS1003", "email.message_from_bytes takes bytes")
        return lo._abi_call("email", attr, [arg], _MESSAGE, "ps_email_from_bytes", span)
    if kind != "str":
        lo.fail(node, "PS1003", "email.message_from_string takes str")
    return lo._abi_call("email", attr, [arg], _MESSAGE, "ps_email_from_string", span)


def _email_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """The message methods the subset answers, refusing the rest by name."""
    assert isinstance(node.func, ast.Attribute)
    attr = node.func.attr
    if attr == "get":
        return _email_get(lo, node, span, receiver)
    if attr == "get_all":
        return _email_get_all(lo, node, span, receiver)
    if attr == "keys":
        return _email_keys(lo, node, span, receiver)
    if attr == "items":
        return _email_items(lo, node, span, receiver)
    if attr == "walk":
        return _email_walk(lo, node, span, receiver)
    if attr == "get_payload":
        return _email_get_payload(lo, node, span, receiver)
    if attr == "get_content_type":
        return _email_get_content_type(lo, node, span, receiver)
    if attr == "get_filename":
        return _email_get_filename(lo, node, span, receiver)
    if attr == "is_multipart":
        return _email_is_multipart(lo, node, span, receiver)
    if attr == "as_string":
        return _email_as_string(lo, node, span, receiver)
    lo.fail(node, "PS2001", f"EmailMessage.{attr} is not in the lowered subset of email")
    raise AssertionError("unreachable")


def _email_get(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`msg.get(name)`: the first value of a header, or None.

    The header lookup is case-insensitive and the value is RFC 2047
    decoded, the way CPython's compat32 `Message.get` reads it. The
    missing-header answer is None, so the lowered form is the tagged
    `str | None`: a hit probe, then the get fills the cell only when the
    probe hit (the shape `shutil.which` and `re.search` lower).
    """
    from pyscriptc.lower._call_attr import _if_node, _opt_fill

    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "Message.get expects 1 argument(s)")
    name = lo._expr(node.args[0])
    if _node_type_kind(name) != "str":
        lo.fail(node, "PS1003", "Message.get name is str")
    opt_t = lo._opt_type(_t_leaf("str"), node)
    res = lo._opt_box(None, opt_t, span)
    dest = res["id"]
    hit = lo._abi_call("email", "get.hit", [receiver, name], _t_leaf("bool"), "ps_email_header_hit", span)
    got = lo._abi_call("email", "get.value", [receiver, name], _t_leaf("str"), "ps_email_header_get", span)
    lo.prelude.append(_if_node(hit, _opt_fill(dest, opt_t, got, span), [], span))
    return res


def _email_get_all(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`msg.get_all(name)`: every value of a header, in wire order.

    A header the message does not carry answers None, the way CPython's
    `get_all` does (the empty list is the `failobj` CPython's own
    default does not use). The answer is the tagged `list[str] | None`:
    a hit probe, then the values fill the cell only when the probe hit,
    the shape `Message.get` and `get_filename` lower onto.
    """
    from pyscriptc.lower._call_attr import _if_node, _opt_fill

    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "Message.get_all expects 1 argument(s)")
    name = lo._expr(node.args[0])
    if _node_type_kind(name) != "str":
        lo.fail(node, "PS1003", "Message.get_all name is str")
    opt_t = lo._opt_type(_LIST_STR, node)
    res = lo._opt_box(None, opt_t, span)
    dest = res["id"]
    hit = lo._abi_call("email", "get_all.hit", [receiver, name], _t_leaf("bool"), "ps_email_header_hit", span)
    values = lo._abi_call("email", "get_all.values", [receiver, name], _LIST_STR, "ps_email_header_values", span)
    lo.prelude.append(_if_node(hit, _opt_fill(dest, opt_t, values, span), [], span))
    return res


def _email_keys(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`msg.keys()`: the header names, in wire order."""
    lo._reject_keywords(node)
    if node.args:
        lo.fail(node, "PS2003", "Message.keys expects 0 argument(s)")
    return lo._abi_call("email", "keys", [receiver], _LIST_STR, "ps_email_keys", span)


def _email_items(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`msg.items()`: the (name, value) pairs, in wire order.

    The pairs are the synthetic two-field tuple class `dict.items` lowers
    onto: the emitter reads the class layout for the pair record, so the
    runtime packs the name and the decoded value at the class's own
    offsets.
    """
    lo._reject_keywords(node)
    if node.args:
        lo.fail(node, "PS2003", "Message.items expects 0 argument(s)")
    elem = lo._tuple_type([_t_leaf("str"), _t_leaf("str")], node)
    return lo._abi_call("email", "items", [receiver], _t_list(elem), "ps_email_items", span)


def _email_walk(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`msg.walk()`: the message and every part below it, in pre-order.

    The parts are an R24 iterator value: the runtime materializes the
    list of the parts and the cursor `for`/`next`/`list` advance reads.
    """
    from pyscriptc.restlower._iterators import _mk_iter

    lo._reject_keywords(node)
    if node.args:
        lo.fail(node, "PS2003", "Message.walk expects 0 argument(s)")
    seq = lo._abi_call("email", "walk", [receiver], _t_list(_MESSAGE), "ps_email_walk", span)
    return _mk_iter(lo, seq, node, span)


def _email_get_payload(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`msg.get_payload(decode=False)`: the payload of the part.

    The decode flag is a constant bool (a computed flag is refused with
    `PS1003`, where CPython reads it at run time). `decode=True` decodes
    the quoted-printable and base64 transfer encodings and answers bytes;
    `decode=False` answers the raw body text. A multipart part has no flat
    payload of its own: CPython returns the list of children there, which
    is outside the closed surface, so the runtime raises at the call.
    """
    decode = False
    if node.keywords:
        if len(node.keywords) != 1 or node.keywords[0].arg != "decode":
            lo.fail(node, "PS2003", "Message.get_payload accepts only decode")
        value = node.keywords[0].value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, bool):
            lo.fail(node, "PS1003", "Message.get_payload decode is a constant bool")
        decode = value.value
    if node.args:
        lo.fail(node, "PS2003", "Message.get_payload expects 0 argument(s)")
    if decode:
        return lo._abi_call("email", "payload.decoded", [receiver], _t_leaf("bytes"), "ps_email_payload_decoded", span)
    return lo._abi_call("email", "payload", [receiver], _t_leaf("str"), "ps_email_payload", span)


def _email_get_content_type(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`msg.get_content_type()`: the lowercased type/subtype word."""
    lo._reject_keywords(node)
    if node.args:
        lo.fail(node, "PS2003", "Message.get_content_type expects 0 argument(s)")
    return lo._abi_call("email", "get_content_type", [receiver], _STR, "ps_email_content_type", span)


def _email_get_filename(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`msg.get_filename()`: the attachment name, or None.

    The name parameter rides Content-Disposition's `filename` and falls
    back to Content-Type's `name`, the way CPython's `get_filename` does.
    The missing-name answer is None: the same tagged optional `get`
    lowers onto.
    """
    from pyscriptc.lower._call_attr import _if_node, _opt_fill

    lo._reject_keywords(node)
    if node.args:
        lo.fail(node, "PS2003", "Message.get_filename expects 0 argument(s)")
    opt_t = lo._opt_type(_t_leaf("str"), node)
    res = lo._opt_box(None, opt_t, span)
    dest = res["id"]
    hit = lo._abi_call("email", "filename.hit", [receiver], _t_leaf("bool"), "ps_email_filename_hit", span)
    got = lo._abi_call("email", "filename.get", [receiver], _t_leaf("str"), "ps_email_filename_get", span)
    lo.prelude.append(_if_node(hit, _opt_fill(dest, opt_t, got, span), [], span))
    return res


def _email_is_multipart(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`msg.is_multipart()`: the Content-Type word is multipart/*."""
    lo._reject_keywords(node)
    if node.args:
        lo.fail(node, "PS2003", "Message.is_multipart expects 0 argument(s)")
    return lo._abi_call("email", "is_multipart", [receiver], _t_leaf("bool"), "ps_email_is_multipart", span)


def _email_as_string(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`msg.as_string()`: the headers in wire order, then the body."""
    lo._reject_keywords(node)
    if node.args:
        lo.fail(node, "PS2003", "Message.as_string expects 0 argument(s)")
    return lo._abi_call("email", "as_string", [receiver], _STR, "ps_email_as_string", span)
