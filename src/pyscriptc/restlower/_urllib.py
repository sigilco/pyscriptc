"""urllib.request call lowering.

`urlopen(url)` lowers to `ps_http_request(url, "GET", b"", "", 0.0) ->
HttpResponse`: the C client in `runtime/ps_http.c` performs the request and
fills the record (status, headers, body). `urlopen(url, data)` posts bytes
and defaults the method to POST. `urlopen(url, timeout=...)` bounds the
whole request; expiry raises TimeoutError like CPython.

`Request(url, data=..., headers=..., method=...)` lowers to the HttpRequest
record: the fields the urlopen call reads. `urlopen(req)` takes the fields
back off the record and emits the request call. The headers field stays the
dict display the script wrote, rendered to the wire form at the urlopen
call; only constant string keys and values are in the closed surface, so
the rendering happens at compile time.

The response shims ride the HttpResponse record: `.status` and `.headers`
and `.body` are fields, `getcode()` reads the status, `info()` reads the
headers, `read()` reads the body. 4xx and 5xx raise HTTPError semantics
through OSError (the class the runtime hierarchy carries); the body stays
readable on the record the runtime filled before raising.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc import _irnode as _ir
from pyscriptc.lower._core import _dict_field, _list_field, _maybe_node_kind, _maybe_type_kind, _show
from pyscriptc.reflection import kwargs_call_note
from pyscriptc.restlower._shared import (
    _class,
    _const_text,
    _field,
    _field_name,
    _field_type,
    _mk_span,
    _node_kind,
    _node_span,
    _node_type,
    _node_type_kind,
    _node_value,
    _type_kind,
    _type_name,
)
from pyscriptc.tirserde import dict_to_node, node_to_dict

_METHODS = {"GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"}


def _str_arg(lo: Any, node: ast.expr, what: str) -> dict[str, Any]:
    arg = lo._expr(node)
    if _node_type_kind(arg) != "str":
        lo.fail(node, "PS1003", f"{what} is " + _show(_node_type(arg)) + ", expected str")
    return arg


def _bytes_arg(lo: Any, node: ast.expr, what: str) -> dict[str, Any]:
    arg = lo._expr(node)
    if _node_type_kind(arg) != "bytes":
        lo.fail(node, "PS1003", f"{what} is " + _show(_node_type(arg)) + ", expected bytes")
    return arg


def _float_arg(lo: Any, node: ast.expr, what: str) -> dict[str, Any]:
    arg = lo._expr(node)
    kind = _node_type_kind(arg)
    if kind not in {"float", "int"}:
        lo.fail(node, "PS1003", f"{what} is " + _show(_node_type(arg)) + ", expected float")
    if kind == "int":
        return node_to_dict(_ir.cast("", dict_to_node(arg), _ir.t_leaf("float"), _mk_span(_node_span(arg))))
    return arg


def _method_arg(lo: Any, node: ast.expr, what: str) -> dict[str, Any]:
    arg = _str_arg(lo, node, what)
    if _node_kind(arg) == "const":
        text = _node_value(arg)
        if text.upper() not in _METHODS:
            lo.fail(node, "PS1003", f"{what} is not an HTTP method: {text}")
        return {**arg, "value": text.upper()}
    return arg


def _headers_arg(lo: Any, node: ast.expr, what: str) -> dict[str, Any] | None:
    """A dict display of headers, rendered to the wire block at compile time.

    Runtime-built header dicts need a C-side renderer over PsMap; the closed
    surface takes the display with constant str keys and values.
    """
    lowered = lo._expr(node)
    lowered_type = _node_type(lowered)
    key_type = _dict_field(lowered_type, "key") or {}
    value_type = _dict_field(lowered_type, "value") or {}
    if (
        _type_kind(lowered_type) != "dict"
        or _maybe_type_kind(key_type) != "str"
        or _maybe_type_kind(value_type) != "str"
    ):
        lo.fail(node, "PS1003", f"{what} is " + _show(lowered_type) + ", expected dict[str, str]")
    if _node_kind(lowered) != "dict":
        lo.fail(node, "PS2001", f"{what} is a dict display with constant str keys and values")
    rows: list[str] = []
    for pair in _list_field(lowered, "pairs"):
        key = _dict_field(pair, "key") or {}
        value = _dict_field(pair, "value") or {}
        if _maybe_node_kind(key) != "const" or _maybe_node_kind(value) != "const":
            lo.fail(node, "PS2001", f"{what} is a dict display with constant str keys and values")
        rows.append(f"{_node_value(key)}: {_node_value(value)}\r\n")
    return _const_text("".join(rows), "str", _node_span(lowered))


def _split_args(
    lo: Any,
    node: ast.Call,
    what: str,
    rest_names: tuple[str, ...],
) -> dict[str, ast.expr | None]:
    """Split a `f(url, ...rest)` call: first positional is the url, the rest
    map positionally (data only) or by keyword."""
    if not node.args:
        lo.fail(node, "PS2003", f"{what} expects a url")
    data = None
    timeout = None
    headers = None
    method = None
    rest = node.args[1:]
    if len(rest) > 1:
        lo.fail(node, "PS2003", f"{what} takes url and data positionally")
    if rest:
        data = rest[0]
    for keyword in node.keywords:
        if keyword.arg is None:
            lo.fail(node, "PS2002", kwargs_call_note())
        if keyword.arg == "data" and data is None:
            data = keyword.value
        elif keyword.arg == "timeout":
            timeout = keyword.value
        elif keyword.arg in rest_names and keyword.arg == "headers":
            headers = keyword.value
        elif keyword.arg == "method":
            method = keyword.value
        else:
            lo.fail(node, "PS2003", f"{what} keyword {keyword.arg} is not lowered")
    return {"url": node.args[0], "data": data, "timeout": timeout, "headers": headers, "method": method}


def _emit_open(
    lo: Any,
    node: ast.Call,
    span: dict[str, Any],
    url: ast.expr,
    data: ast.expr | None,
    timeout: ast.expr | None,
    headers: ast.expr | None,
    method: ast.expr | None,
) -> dict[str, Any]:
    url_arg = lo._expr(url)
    url_type = _node_type(url_arg)
    if _type_kind(url_type) == "class" and _type_name(url_type) == "HttpRequest":
        # urlopen(req): the fields come back off the record, headers included.
        args = [
            _record_field(lo, node, url_arg, "url", span),
            _record_field(lo, node, url_arg, "method", span),
            _record_field(lo, node, url_arg, "body", span),
            _record_field(lo, node, url_arg, "headers", span),
            _record_field(lo, node, url_arg, "timeout", span),
        ]
        return lo._abi_call("urllib.request", "urlopen", args, _class("HttpResponse"), "ps_http_request", span)
    return _emit_open_url(lo, node, span, url_arg, data, timeout, headers, method)


def _record_field(lo: Any, node: ast.AST, record: dict[str, Any], name: str, span: dict[str, Any]) -> dict[str, Any]:
    info = lo.classes.get("HttpRequest")
    if info is None:
        lo.fail(node, "PS2001", "HttpRequest has no fields")
    for field in info["fields"]:
        if _field_name(field) == name:
            return _field(record, name, _field_type(field), span)
    lo.fail(node, "PS2001", f"HttpRequest has no field {name}")
    raise AssertionError("unreachable")


def _emit_open_url(
    lo: Any,
    node: ast.Call,
    span: dict[str, Any],
    url_arg: dict[str, Any],
    data: ast.expr | None,
    timeout: ast.expr | None,
    headers: ast.expr | None,
    method: ast.expr | None,
) -> dict[str, Any]:
    if _node_type_kind(url_arg) != "str":
        lo.fail(node, "PS1003", "urlopen url is " + _show(_node_type(url_arg)) + ", expected str or Request")
    body = _bytes_arg(lo, data, "urlopen data") if data is not None else None
    time_arg = _float_arg(lo, timeout, "urlopen timeout") if timeout is not None else None
    header_block = _headers_arg(lo, headers, "urlopen headers") if headers is not None else None
    method_arg = _method_arg(lo, method, "Request method") if method is not None else None
    if method_arg is None:
        default = "POST" if body is not None else "GET"
        method_arg = _const_text(default, "str", _node_span(url_arg))
    empty_bytes = _const_text("", "bytes", span)
    empty_str = _const_text("", "str", span)
    zero = _const_text("0.0", "float", span)
    args = [
        url_arg,
        method_arg,
        body or empty_bytes,
        header_block or empty_str,
        time_arg or zero,
    ]
    return lo._abi_call("urllib.request", "urlopen", args, _class("HttpResponse"), "ps_http_request", span)


def _request_record(
    lo: Any,
    node: ast.Call,
    span: dict[str, Any],
    fields: dict[str, ast.expr | None],
) -> dict[str, Any]:
    """Build the HttpRequest record the urlopen call reads back."""
    url_arg = _str_arg(lo, fields["url"], "Request url")
    body = _bytes_arg(lo, fields["data"], "Request data") if fields["data"] is not None else None
    time_arg = _float_arg(lo, fields["timeout"], "Request timeout") if fields["timeout"] is not None else None
    method_arg = _method_arg(lo, fields["method"], "Request method") if fields["method"] is not None else None
    if method_arg is None:
        default = "POST" if body is not None else "GET"
        method_arg = _const_text(default, "str", span)
    empty_bytes = _const_text("", "bytes", span)
    empty_str = _const_text("", "str", span)
    zero = _const_text("0.0", "float", span)
    header_block = _headers_arg(lo, fields["headers"], "Request headers") if fields["headers"] is not None else None
    return lo._abi_call(
        "urllib.request",
        "Request",
        [
            url_arg,
            method_arg,
            body or empty_bytes,
            header_block or empty_str,
            time_arg or zero,
        ],
        _class("HttpRequest"),
        "ps_http_request_new",
        span,
    )


def _urllib_request_call(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None, attr: str
) -> dict[str, Any]:
    if attr == "urlopen":
        fields = _split_args(lo, node, "urlopen", ("headers",))
        if fields["headers"] is not None:
            lo.fail(node, "PS2003", "urlopen keyword headers is not lowered (put headers on the Request)")
        return _emit_open(lo, node, span, fields["url"], fields["data"], fields["timeout"], None, None)
    if attr == "Request":
        fields = _split_args(lo, node, "Request", ("headers",))
        return _request_record(lo, node, span, fields)
    lo.fail(node, "PS2001", f"urllib.request.{attr} is not in the lowered subset of urllib.request")
    raise AssertionError("unreachable")
