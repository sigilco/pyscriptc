# SPDX-License-Identifier: Apache-2.0
"""http.server: the handler subset (issue #1270, slice 2).

The closed surface is `HTTPServer`, a `BaseHTTPRequestHandler` subclass
with `do_GET` and `do_POST`, `send_response`, `send_header`,
`end_headers`, `wfile.write`, `rfile.read(n)`, `headers.get`, `path`,
`serve_forever`, `handle_request` and `shutdown`. The runtime
(`ps_http_server.c`) owns the socket, the request-line and header parsing
and the response bytes; this module owns the shape of the calls and the
fences.

The split follows the runtime file's own contract: the runtime answers
the method name of the request it just parsed, and the lowering turns
that name into the user's `do_GET` / `do_POST` call through the ordinary
class-method lowering. No new dispatch mechanism is introduced and the
handler stays a lowered user class, not a runtime record.

`serve_forever()` therefore lowers to a loop in the emitted IR:

    instance = Handler()
    while not stop:
        request = ps_httpsrv_serve_one(server)   # accept, parse, read the body
        if ps_httpsrv_stopped(server):           # the server was shut down
            stop = True
        else:
            response = ps_httpresp_new()         # the response record
            instance._request = request          # the context `self` reads
            instance._response = response
            if ps_httpreq_method(request) == "GET":
                cls.Handler.do_GET(instance)     # the ordinary method call
            ps_httpsrv_finish(request, response)  # write the response, close

The handler instance is created by the lowering, so `self` is an ordinary
class instance and the request-backed attributes (`self.path`,
`self.rfile`, `self.wfile`, `self.headers`) read through the records the
loop parks in `_request` and `_response`. A field the program assigns
itself (`self.data = ...`) is an ordinary class field.

The handler class is keyed by the construction call: `HTTPServer(addr,
Handler)` records the class name under the local the server was assigned
to, and the method call reads it back. The server value is a runtime
record, so the mapping cannot ride the record itself.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc import _irnode as _ir
from pyscriptc.restlower._shared import (
    _asg,
    _class,
    _cmp,
    _expr_stmt,
    _field,
    _if_stmt,
    _mk_span,
    _nm,
    _node_type_kind,
    _set_field,
    _t_leaf,
    _tname,
    _while_stmt,
)
from pyscriptc.tirserde import dict_to_node, dict_to_type, node_to_dict

_SERVER = _class("HTTPServer")
_REQUEST = _class("HttpServerRequest")
_HEADERS = _class("HttpServerHeaders")
_READER = _class("HttpServerReader")
_WRITER = _class("HttpServerWriter")
_RESPONSE = _class("HttpServerResponse")
_INT = _t_leaf("int")
_NONE = _t_leaf("none")
_STR = _t_leaf("str")
_BYTES = _t_leaf("bytes")
_BOOL = _t_leaf("bool")

# The handler methods the subset dispatches on, with the request method
# each answers. The runtime reads the method off the request line; the
# lowering maps it onto one of these.
HANDLER_METHODS = ("do_GET", "do_POST")
_METHOD_FOR = {"do_GET": "GET", "do_POST": "POST"}

# The base class the user's handler extends. The handler's request-backed
# attributes read through the records the loop parks in `_request` and
# `_response`.
HANDLER_BASE = "BaseHTTPRequestHandler"

# The request-backed attributes of the handler base, with the record each
# one reads through. `path` and `headers` read the request; `rfile` and
# `wfile` are the request's body reader and the response's body writer.
_REQUEST_ATTRS = ("path", "headers", "rfile", "wfile")


def _httpserver_call(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None, attr: str
) -> dict[str, Any]:
    """`http.server.HTTPServer(addr, handler)`."""
    if attr != "HTTPServer":
        lo.fail(node, "PS2001", f"http.server.{attr} is not in the lowered subset of http.server")
    lo._reject_keywords(node)
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "http.server.HTTPServer expects 2 argument(s)")
    host, port = _endpoint(lo, node.args[0], "HTTPServer")
    handler = _handler_class(lo, node.args[1])
    lo.mobile_symbol_guard(node, "ps_httpsrv_new")
    value = lo._abi_call("http.server", "HTTPServer", [host, port], _SERVER, "ps_httpsrv_new", span)
    # The handler class rides the call node: the server value is a runtime
    # record, so the class name cannot live in it. The call node's `name`
    # field carries it, the assignment copies it onto the local, and the
    # method call reads it back.
    value["name"] = handler
    return value


def _endpoint(lo: Any, arg: ast.expr, what: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The (host, port) argument: the tuple display only, a const or
    variable str host and an int port. The same shape the socket subset
    takes for `bind`."""
    if not isinstance(arg, ast.Tuple) or len(arg.elts) != 2:
        lo.fail(arg, "PS1003", f"http.server.{what} takes a (host, port) tuple")
    host = lo._expr(arg.elts[0])
    port = lo._expr(arg.elts[1])
    if _node_type_kind(host) != "str":
        lo.fail(arg.elts[0], "PS1003", f"http.server.{what} host is str")
    if _node_type_kind(port) != "int":
        lo.fail(arg.elts[1], "PS1003", f"http.server.{what} port is int")
    return host, port


def _handler_class(lo: Any, arg: ast.expr) -> str:
    """The handler class argument: the name of a lowered class that
    extends the base and defines at least one of the handler methods."""
    if not isinstance(arg, ast.Name):
        lo.fail(arg, "PS1003", "http.server.HTTPServer handler is a handler class")
    name = arg.id
    info = lo.classes.get(name)
    if info is None:
        # A module-scope assignment lowers before the class pass fills the
        # class table, so the handler class may only exist as its AST. The
        # scan answers the same three facts the table would: the class is
        # one of the module's own, it extends the handler base, and it
        # defines at least one of the handler methods.
        info = _ast_handler_info(lo, name)
        if info is None:
            lo.fail(arg, "PS1003", "http.server.HTTPServer handler is a handler class")
    if info.get("base") != HANDLER_BASE:
        lo.fail(arg, "PS2002", "the http.server handler class extends BaseHTTPRequestHandler")
    methods = info.get("methods", {})
    if not any(method in methods for method in HANDLER_METHODS):
        lo.fail(arg, "PS2002", "the http.server handler class defines no do_GET or do_POST")
    return name


def _ast_handler_info(lo: Any, name: str) -> dict[str, Any] | None:
    """The handler facts of a class the class table does not carry yet.

    The module's own `ast.ClassDef` nodes are the source: the base spelling
    resolves the same way the class pass resolves it, and the method names
    are the body's `FunctionDef`s. None means the module defines no such
    class.
    """
    nodes = getattr(lo, "module_class_nodes", None) or []
    node = next((candidate for candidate in nodes if candidate.name == name), None)
    if node is None:
        return None
    if len(node.bases) != 1 or lo._http_handler_base_name(node.bases[0]) is None:
        return None
    methods = {stmt.name for stmt in node.body if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return {"base": HANDLER_BASE, "methods": methods}


def _no_args(lo: Any, node: ast.Call, what: str) -> None:
    if node.args:
        lo.fail(node, "PS2003", f"{what} expects 0 argument(s)")


def _httpserver_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`server.serve_forever()`, `server.handle_request()`,
    `server.shutdown()`, `server.server_close()`."""
    assert isinstance(node.func, ast.Attribute)
    attr = node.func.attr
    if attr == "serve_forever":
        return _serve_forever(lo, node, span, receiver, forever=True)
    if attr == "handle_request":
        return _serve_forever(lo, node, span, receiver, forever=False)
    if attr == "shutdown":
        lo._reject_keywords(node)
        _no_args(lo, node, "HTTPServer.shutdown")
        lo.mobile_symbol_guard(node, "ps_httpsrv_shutdown")
        return lo._abi_call("http.server", "shutdown", [receiver], _NONE, "ps_httpsrv_shutdown", span)
    if attr == "server_close":
        lo._reject_keywords(node)
        _no_args(lo, node, "HTTPServer.server_close")
        lo.mobile_symbol_guard(node, "ps_httpsrv_close")
        return lo._abi_call("http.server", "server_close", [receiver], _NONE, "ps_httpsrv_close", span)
    lo.fail(node, "PS2001", f"HTTPServer.{attr} is not in the lowered subset of http.server")
    raise AssertionError("unreachable")


def server_attr(lo: Any, node: ast.Attribute, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any] | None:
    """`server.server_port` and `server.server_address`: the bound address.

    CPython's `HTTPServer` carries the bound address as two attributes:
    `server_address` is the `(host, port)` pair and `server_port` the port
    on its own. The runtime holds the same two halves, so each read is a
    runtime call over the server record. Port 0 lets the kernel pick, so a
    program reads the assigned port back through `server_port`.

    None means the attribute is not one of the two, so the caller falls
    through to the ordinary field read.
    """
    attr = node.attr
    if attr == "server_port":
        lo.mobile_symbol_guard(node, "ps_httpsrv_port")
        return lo._abi_call("http.server", "port_of", [receiver], _INT, "ps_httpsrv_port", span)
    if attr == "server_address":
        lo.mobile_symbol_guard(node, "ps_httpsrv_host")
        host = lo._abi_call("http.server", "host_of", [receiver], _STR, "ps_httpsrv_host", span)
        port = lo._abi_call("http.server", "port_of", [receiver], _INT, "ps_httpsrv_port", span)
        return lo._materialize_tuple([host, port], span, node)
    return None


def _serve_forever(
    lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any], forever: bool
) -> dict[str, Any]:
    """`server.serve_forever()` and `server.handle_request()`: the accept
    loop, lowered into the IR.

    The runtime owns the accept, the request parsing and the response
    bytes. The dispatch is the lowering's: the loop reads the request the
    runtime parsed, picks the handler method off the request's method
    name, and calls it through the ordinary class-method lowering, with
    the handler instance as the call's `self`.

    The loop is the IR's flag-break while: the request stops the loop
    when the server was asked to shut down, and the flag ends the body
    so the read is the loop's own condition. `handle_request` serves one
    request and returns, so its loop runs once.
    """
    lo._reject_keywords(node)
    _no_args(lo, node, "HTTPServer.serve_forever" if forever else "HTTPServer.handle_request")
    lo.mobile_symbol_guard(node, "ps_httpsrv_serve_one")
    handler = _server_handler_class(lo, receiver)
    if handler is None:
        lo.fail(node, "PS2002", "the http server has no handler class")
    info = lo.classes.get(handler, {})
    methods = info.get("methods", {})
    # The handler instance: an ordinary class instance, created here so the
    # method call has its `self`. The instance is one per serve_forever,
    # the way CPython's BaseHTTPRequestHandler is one per request with the
    # connection state on it; the state the handler reads resets per
    # request below.
    instance_t = _tname(lo, "httpsrv_self")
    lo.locals[instance_t] = _class(handler)
    instance = _nm(instance_t, _class(handler), span)
    # The request: the runtime hands the parsed request back as an opaque
    # pointer, and the loop parks it in the instance's `_request` field.
    request_t = _tname(lo, "httpsrv_req")
    lo.locals[request_t] = _REQUEST
    request = _nm(request_t, _REQUEST, span)
    # The response: the handler's response record, one per request.
    response_t = _tname(lo, "httpsrv_resp")
    lo.locals[response_t] = _RESPONSE
    response = _nm(response_t, _RESPONSE, span)
    # The stop flag: the flag-break while's own condition.
    stop_t = _tname(lo, "httpsrv_stop")
    lo.locals[stop_t] = _BOOL
    stop = _nm(stop_t, _BOOL, span)
    # The request's method name: the runtime parsed it off the request line.
    method_t = _tname(lo, "httpsrv_method")
    lo.locals[method_t] = _STR

    body: list[dict[str, Any]] = []
    # The request: the runtime accepts, parses and reads the body.
    body.append(
        _asg(
            request_t,
            lo._abi_call("http.server", "serve_one", [receiver], _REQUEST, "ps_httpsrv_serve_one", span),
            span,
        )
    )
    # The response record: one per request, the way CPython builds one
    # response per connection.
    body.append(
        _asg(
            response_t,
            lo._abi_call("http.server", "response_new", [], _RESPONSE, "ps_httpresp_new", span),
            span,
        )
    )
    # The stop test: the runtime answers 1 when the server was shut down.
    stopped = lo._abi_call("http.server", "stopped", [receiver], _INT, "ps_httpsrv_stopped", span)
    stop_check = _cmp("!=", stopped, _const_int(0, span), span)
    body.append(_if_stmt(stop_check, [_asg(stop_t, _const_bool(True, span), span)], [], span))
    # The handler reads the request through the instance's `_request` field
    # and writes through the instance's `_response` field.
    fill_check = _cmp("==", stopped, _const_int(0, span), span)
    fill = [_set_field(instance, "_request", request, span), _set_field(instance, "_response", response, span)]
    body.append(_if_stmt(fill_check, fill, [], span))
    # The dispatch: the request's method name picks the handler method.
    dispatch: list[dict[str, Any]] = []
    for method in HANDLER_METHODS:
        if method not in methods:
            continue
        body.append(
            _asg(
                method_t,
                lo._abi_call("http.server", "method_of", [request], _STR, "ps_httpreq_method", span),
                span,
            )
        )
        call = node_to_dict(
            _ir.call(
                _ir.function_callee(f"cls.{handler}.{method}"),
                [dict_to_node(instance)],
                dict_to_type(_NONE),
                _mk_span(span),
                "",
                "",
                -1,
            )
        )
        dispatch.append(
            _if_stmt(
                _cmp("==", _nm(method_t, _STR, span), _const_text(_METHOD_FOR[method], span), span),
                [_expr_stmt(call, span)],
                [],
                span,
            )
        )
    # The response bytes go back on the wire and the connection closes.
    dispatch.append(
        _expr_stmt(lo._abi_call("http.server", "finish", [request, response], _NONE, "ps_httpsrv_finish", span), span)
    )
    body.extend(dispatch)
    lo.prelude.append(_asg(instance_t, _instance_of(handler, span), span))
    if forever:
        # `serve_forever` is the flag-break while: the loop runs while the
        # stop flag is false, the way `_user_while` spells it. The flag
        # ends the loop after the runtime answers that the server was shut
        # down (CPython's serve_forever/shutdown pair).
        lo.prelude.append(_asg(stop_t, _const_bool(False, span), span))
        lo.prelude.append(_while_stmt(_not(stop, span), body, span))
    else:
        # `handle_request` serves exactly one request: the body runs once
        # and there is no loop to run again.
        lo.prelude.extend(body)
    return _const_none(span)


def _instance_of(handler: str, span: dict[str, Any]) -> dict[str, Any]:
    """The handler instance the loop drives: an ordinary construction."""
    return node_to_dict(
        _ir.call(
            _ir.class_callee(handler),
            [],
            dict_to_type(_class(handler)),
            _mk_span(span),
            "",
            "",
            -1,
        )
    )


def _not(operand: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The `not` unary."""
    return node_to_dict(_ir.unary("not", dict_to_node(operand), dict_to_type(_BOOL), _mk_span(span)))


def _const_none(span: dict[str, Any]) -> dict[str, Any]:
    """The `None` const node a None-returning call spells."""
    return node_to_dict(_ir.const_node(_ir.P_NONE, "", _ir.t_leaf("none"), _mk_span(span)))


def _const_bool(value: bool, span: dict[str, Any]) -> dict[str, Any]:
    """The bool const node."""
    return node_to_dict(_ir.const_node(_ir.P_BOOL, "true" if value else "false", _ir.t_leaf("bool"), _mk_span(span)))


def _const_int(value: int, span: dict[str, Any]) -> dict[str, Any]:
    """The int const node."""
    return node_to_dict(_ir.const_node(_ir.P_INT, str(value), _ir.t_leaf("int"), _mk_span(span)))


def _const_text(value: str, span: dict[str, Any]) -> dict[str, Any]:
    """The str const node."""
    return node_to_dict(_ir.const_node(_ir.P_STR, value, _ir.t_leaf("str"), _mk_span(span)))


def _server_handler_class(lo: Any, receiver: dict[str, Any]) -> str | None:
    """The handler class of one server value.

    The server value is a runtime record, so the handler class rides the
    compile-time binding the server was built from: the construction call
    records the class name under the local the server was assigned to,
    and the method call reads it back.
    """
    name = receiver.get("id")
    if name is None:
        return None
    return lo.httpsrv_handlers.get(name)


# ----- the handler side -----

# The response methods the handler calls on itself. Each one reads the
# instance's `_response` record, the way CPython's handler writes into the
# response the server handed it.
_RESPONSE_METHODS = ("send_response", "send_header", "end_headers")


def handler_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any] | None:
    """`self.send_response(...)`, `self.send_header(...)`,
    `self.end_headers()` on a handler instance.

    The handler's `self` is an ordinary class instance, so the call is a
    method call on the user's class; the base's three response methods
    are not user methods, so they lower here: each one is a runtime call
    over the instance's `_response` record.

    None means the call is not one of the handler methods, so the caller
    falls through to the ordinary method lowering.
    """
    attr = node.func.attr if isinstance(node.func, ast.Attribute) else ""
    if attr not in _RESPONSE_METHODS:
        return None
    lo.mobile_guard(node, f"BaseHTTPRequestHandler.{attr}", "httpsrv")
    lo._reject_keywords(node)
    response = _field(receiver, "_response", _RESPONSE, span)
    if attr == "send_response":
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "BaseHTTPRequestHandler.send_response expects 1 argument(s)")
        status = lo._expr(node.args[0])
        if _node_type_kind(status) != "int":
            lo.fail(node.args[0], "PS1003", "BaseHTTPRequestHandler.send_response status is int")
        lo.prelude.append(
            _expr_stmt(
                lo._abi_call(
                    "http.server", "send_response", [response, status], _NONE, "ps_httpresp_send_response", span
                ),
                span,
            )
        )
        return _const_none(span)
    if attr == "send_header":
        if len(node.args) != 2:
            lo.fail(node, "PS2003", "BaseHTTPRequestHandler.send_header expects 2 argument(s)")
        name = lo._expr(node.args[0])
        value = lo._expr(node.args[1])
        if _node_type_kind(name) != "str":
            lo.fail(node.args[0], "PS1003", "BaseHTTPRequestHandler.send_header name is str")
        if _node_type_kind(value) != "str":
            lo.fail(node.args[1], "PS1003", "BaseHTTPRequestHandler.send_header value is str")
        lo.prelude.append(
            _expr_stmt(
                lo._abi_call(
                    "http.server", "send_header", [response, name, value], _NONE, "ps_httpresp_send_header", span
                ),
                span,
            )
        )
        return _const_none(span)
    if node.args:
        lo.fail(node, "PS2003", "BaseHTTPRequestHandler.end_headers expects 0 argument(s)")
    lo.prelude.append(
        _expr_stmt(lo._abi_call("http.server", "end_headers", [response], _NONE, "ps_httpresp_end_headers", span), span)
    )
    return _const_none(span)


def handler_attr(lo: Any, node: ast.Attribute, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any] | None:
    """`self.path`, `self.headers`, `self.rfile`, `self.wfile` on a
    handler instance.

    `path` reads the request's path; `headers`, `rfile` and `wfile` are
    the request's header block, body reader and the response's body
    writer. Each one is a runtime call over the instance's `_request` or
    `_response` record.

    None means the attribute is not one of the request-backed ones, so
    the caller falls through to the ordinary field read.
    """
    attr = node.attr
    if attr == "path":
        lo.mobile_guard(node, "BaseHTTPRequestHandler.path", "httpsrv")
        request = _field(receiver, "_request", _REQUEST, span)
        return lo._abi_call("http.server", "path_of", [request], _STR, "ps_httpreq_path", span)
    if attr == "headers":
        lo.mobile_guard(node, "BaseHTTPRequestHandler.headers", "httpsrv")
        request = _field(receiver, "_request", _REQUEST, span)
        return lo._abi_call("http.server", "headers_of", [request], _HEADERS, "ps_httpreq_headers", span)
    if attr == "rfile":
        lo.mobile_guard(node, "BaseHTTPRequestHandler.rfile", "httpsrv")
        request = _field(receiver, "_request", _REQUEST, span)
        return lo._abi_call("http.server", "rfile_of", [request], _READER, "ps_httpreq_rfile", span)
    if attr == "wfile":
        lo.mobile_guard(node, "BaseHTTPRequestHandler.wfile", "httpsrv")
        response = _field(receiver, "_response", _RESPONSE, span)
        return lo._abi_call("http.server", "wfile_of", [response], _WRITER, "ps_httpresp_wfile", span)
    return None


def headers_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any] | None:
    """`self.headers.get(name)`: the header's value, case-insensitively.

    CPython's `email.Message.get` answers None for an absent header; the
    closed subset answers the empty string instead, so the read has one
    type and the program compares it with a str (divergence 80).
    """
    if node.func.attr != "get":
        return None
    lo.mobile_guard(node, "BaseHTTPRequestHandler.headers.get", "httpsrv")
    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "HttpHeaders.get expects 1 argument(s)")
    name = lo._expr(node.args[0])
    if _node_type_kind(name) != "str":
        lo.fail(node.args[0], "PS1003", "HttpHeaders.get name is str")
    return lo._abi_call("http.server", "header_get", [receiver, name], _STR, "ps_httpreq_header", span)


def reader_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any] | None:
    """`self.rfile.read(n)`: the request's body bytes."""
    if node.func.attr != "read":
        return None
    lo.mobile_guard(node, "BaseHTTPRequestHandler.rfile.read", "httpsrv")
    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "HttpReader.read expects 1 argument(s)")
    size = lo._expr(node.args[0])
    if _node_type_kind(size) != "int":
        lo.fail(node.args[0], "PS1003", "HttpReader.read size is int")
    return lo._abi_call("http.server", "rfile_read", [receiver, size], _BYTES, "ps_httpreq_rfile_read", span)


def writer_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any] | None:
    """`self.wfile.write(data)`: the response's body bytes."""
    if node.func.attr != "write":
        return None
    lo.mobile_guard(node, "BaseHTTPRequestHandler.wfile.write", "httpsrv")
    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "HttpWriter.write expects 1 argument(s)")
    data = lo._expr(node.args[0])
    if _node_type_kind(data) not in {"bytes", "str"}:
        lo.fail(node.args[0], "PS1003", "HttpWriter.write data is bytes")
    lo.prelude.append(
        _expr_stmt(
            lo._abi_call("http.server", "wfile_write", [receiver, data], _INT, "ps_httpresp_wfile_write", span), span
        )
    )
    return _const_none(span)
