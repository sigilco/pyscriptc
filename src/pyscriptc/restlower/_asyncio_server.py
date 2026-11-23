# SPDX-License-Identifier: Apache-2.0
"""asyncio.start_server and the Server surface (issue #1281, slice 2).

`start_server(callback, host, port)` binds a listening socket and answers
CPython's `Server` handle. The callback is compiled code, so it lives in
the frame the serve loop drives: the `Server` class carries two fields,
the callback (`func`) and the runtime record pointer (the listening
socket, the closed flag and the peer the last accept produced).

`serve_forever()` is CPython's awaitable, so it lowers to a marker the
coroutine pass expands into the park-and-suspend the accept needs
(`__ps_await_prim__` with the `server_accept` operation): the runtime
takes the peer when one is waiting and parks the task on the listen
descriptor when none is. The callback call itself is compiled code: the
serve loop is the compiled `while` the marker sits in, so the callback
runs in the user's own frame, with the (reader, writer) pair the
`open_connection` slice builds.

Everything outside the closed surface fences: `start_server` takes
exactly three positional arguments (the `ssl` / `family` / `flags` /
`backlog` / `reuse_address` keywords are outside the subset), the
server's `sockets`, `start_serving`, `is_serving` and the rest.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc import _irnode as _ir
from pyscriptc.coro import PRIM_MARKER, enabled
from pyscriptc.lower._expr import _value_call
from pyscriptc.restlower._shared import (
    _asg,
    _call_builtin,
    _class,
    _expr_stmt,
    _list_lit,
    _mk_span,
    _nm,
    _node_type,
    _t_leaf,
    _type_kind,
    _while_stmt,
)
from pyscriptc.tirserde import dict_to_node, dict_to_type, node_to_dict

# The server handle: the runtime record pointer, the same shape the
# stream handles use (the runtime owns the listening socket).
_SERVER = _class("Server")
# The accepted peer's record: the same class the reader and the writer
# halves of `open_connection` share.
_STREAM = _class("StreamReader")
_READER = _class("StreamReader")
_WRITER = _class("StreamWriter")
_NONE = _t_leaf("none")
_BOOL = _t_leaf("bool")
_INT = _t_leaf("int")
# CPython's `server.sockets` is a tuple of socket handles; the annotation
# `tuple[socket.socket, ...]` erases to a list, so the lowering answers a
# list of the socket handle class.
_SOCK = _class("Socket")
_SOCK_LIST = {"kind": "list", "elem": _SOCK}

# The callback value the server record carries: the (reader, writer)
# pair. The return is the compiled coroutine's own frame type, which the
# serve loop fills in per call site (the callback the program passes is
# known statically, so its frame class is known too).
_CALLBACK_PARAMS = [{"kind": "class", "name": "StreamReader"}, {"kind": "class", "name": "StreamWriter"}]
_CALLBACK = {"kind": "func", "params": _CALLBACK_PARAMS, "return": {"kind": "none"}}
# The frame the callback call answers and the task that drives it: the
# same shapes `asyncio.create_task` builds (the frame class names the
# coroutine, the task wraps one frame).
_FRAME = {"kind": "class", "name": "coro.frame"}
_TASK = {"kind": "class", "name": "Task", "elem": {"kind": "none"}}

# The PRIM operation the serve loop parks on: the runtime accepts a peer
# or parks the running task on the listen descriptor.
_ACCEPT_OP = "server_accept"


def server_attr(lo: Any, node: ast.Attribute, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any] | None:
    """`server.sockets`: the bound listening socket as a one-element list.

    CPython's `Server` carries a tuple of `socket.socket` handles, and the
    annotation `tuple[socket.socket, ...]` erases to a list, so the read
    answers a list with the one handle this subset binds. A program that
    binds port 0 reads the assigned port back through the socket's own
    `getsockname`.

    None means the attribute is not in the subset, so the caller falls
    through to the ordinary field read (and its fence).
    """
    if node.attr != "sockets":
        return None
    lo.mobile_guard(node, "Server.sockets", "sock")
    sock = lo._abi_call("asyncio", "Server.socket", [receiver], _SOCK, "ps_server_socket", span)
    return _list_lit([sock], _SOCK, span)


def _endpoint_host(lo: Any, value: ast.expr, what: str) -> dict[str, Any]:
    """The host argument: a str. CPython's `None` spelling (the loop
    default) is outside the closed three-arg form."""
    host = lo._expr(value)
    if _type_kind(host["type"]) != "str":
        lo.fail(value, "PS1003", f"{what} host is str")
    return host


def _endpoint_port(lo: Any, value: ast.expr, what: str) -> dict[str, Any]:
    """The port argument: an int."""
    port = lo._expr(value)
    if _type_kind(port["type"]) != "int":
        lo.fail(value, "PS1003", f"{what} port is int")
    return port


def _callback(lo: Any, node: ast.expr, span: dict[str, Any]) -> dict[str, Any]:
    """The client callback: a callable value.

    CPython's callback takes the (reader, writer) pair. The compiled
    value is a `func` whose parameters are the pair's two halves and
    whose return is the callback's own result (None for the shape the
    subset lowers).
    """
    value = lo._expr(node)
    ftype = _node_type(value)
    if _type_kind(ftype) != "func":
        lo.fail(node, "PS1003", "asyncio.start_server takes a callable")
    params = ftype.get("params") or []
    if len(params) != 2:
        lo.fail(node, "PS2003", "asyncio.start_server takes a callback of 2 argument(s)")
    if _type_kind(params[0]) != "class" or params[0].get("name") != "StreamReader":
        lo.fail(node, "PS1003", "the asyncio.start_server callback takes a StreamReader")
    if _type_kind(params[1]) != "class" or params[1].get("name") != "StreamWriter":
        lo.fail(node, "PS1003", "the asyncio.start_server callback takes a StreamWriter")
    return value


def _start_server(lo: Any, node: ast.Call, span: dict[str, Any], expect: Any = None, attr: str = "") -> dict[str, Any]:
    """`asyncio.start_server(callback, host, port)`: bind and listen.

    The server handle is a class with two fields: the callback (a
    `func`) and the runtime record pointer (the listening socket). The
    class is allocated by the runtime's `ps_server_open`, which owns the
    record; the callback is stored into the handle right after, so the
    serve loop can call it.

    The runtime symbols live in the socket file the mobile archive
    excludes, so the call fences there like `socket.bind` does.
    """
    if len(node.args) != 3:
        lo.fail(node, "PS2003", "asyncio.start_server expects 3 argument(s)")
    for keyword in node.keywords:
        lo.fail(keyword.value, "PS2003", f"asyncio.start_server has no keyword argument {keyword.arg}")
    lo.mobile_guard(node, "asyncio.start_server", "sock")
    callback = _callback(lo, node.args[0], span)
    host = _endpoint_host(lo, node.args[1], "asyncio.start_server")
    port = _endpoint_port(lo, node.args[2], "asyncio.start_server")
    # The callback is pinned first: the closure cell it names has to
    # outlive the call, and the serve loop reads it out of the handle.
    cb_slot = lo._temp("cb")
    cb_t = _node_type(callback)
    lo.locals[cb_slot] = cb_t
    lo.prelude.append(_asg(cb_slot, callback, span))
    pinned = _nm(cb_slot, cb_t, span)
    # The record: the runtime owns the listening socket, the value is its
    # pointer.
    record = lo._abi_call("asyncio", "start_server", [host, port], _SERVER, "ps_server_open", span)
    # The callback's frame class rides the call node: the server value is a
    # runtime record, so the frame type cannot live in it. The call node's
    # `server_frame` field carries it, the binding copies it under the
    # local the server was assigned to, and the serve loop reads it back.
    record["server_frame"] = _callback_frame(cb_t)
    rec_slot = lo._temp("srv")
    lo.locals[rec_slot] = _SERVER
    lo.prelude.append(_asg(rec_slot, record, span))
    # The callback rides the record: the runtime holds the callable pair
    # as an opaque pointer, and the serve loop reads it back.
    lo.prelude.append(
        _expr_stmt(
            lo._abi_call(
                "asyncio",
                "Server.set_callback",
                [_nm(rec_slot, _SERVER, span), pinned],
                _NONE,
                "ps_server_set_callback",
                span,
            ),
            span,
        )
    )
    return _nm(rec_slot, _SERVER, span)


def _callback_frame(cb_t: dict[str, Any]) -> str:
    """The frame class the callback call answers.

    A compiled coroutine's callable cell returns its frame class
    (`coro.<name>`), so the callback's return type is the frame the serve
    loop turns into a task. A callback that returns None (a plain
    function) has no frame: the serve loop calls it and drops the answer.
    """
    ret = cb_t.get("return") or {}
    if ret.get("kind") == "class" and str(ret.get("name", "")).startswith("coro."):
        return str(ret["name"])
    return ""


def _server_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """Method dispatch for Server receivers."""
    assert isinstance(node.func, ast.Attribute)
    attr = node.func.attr

    def call(typ: dict[str, Any], symbol: str, args: list[dict[str, Any]], what: str) -> dict[str, Any]:
        return lo._abi_call("asyncio", what, [receiver, *args], typ, symbol, span)

    if attr == "close":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "Server.close", "sock")
        if node.args:
            lo.fail(node, "PS2003", "Server.close expects 0 argument(s)")
        return call(_NONE, "ps_server_close", [], "close")
    if attr == "is_serving":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "Server.is_serving", "sock")
        if node.args:
            lo.fail(node, "PS2003", "Server.is_serving expects 0 argument(s)")
        return call(_BOOL, "ps_server_is_serving", [], "is_serving")
    if attr == "serve_forever":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "Server.serve_forever", "sock")
        if node.args:
            lo.fail(node, "PS2003", "Server.serve_forever expects 0 argument(s)")
        return _serve_forever(lo, node, span, receiver)
    lo.fail(node, "PS2001", f"Server.{attr} is not in the lowered subset of asyncio.server")
    raise AssertionError("unreachable")


def _const_bool(value: bool, span: dict[str, Any]) -> dict[str, Any]:
    """The bool const node."""
    return node_to_dict(_ir.const_node(_ir.P_BOOL, "true" if value else "false", _ir.t_leaf("bool"), _mk_span(span)))


def _not(operand: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """The `not` unary."""
    return node_to_dict(_ir.unary("not", dict_to_node(operand), dict_to_type(_BOOL), _mk_span(span)))


def _server_frame(lo: Any, receiver: dict[str, Any]) -> str:
    """The callback frame class of one server value.

    The server value is a runtime record, so the frame class rides the
    compile-time binding the server was built from: the construction call
    records the frame class name under the local the server was assigned
    to, and the method call reads it back.
    """
    name = receiver.get("id")
    if name is None:
        return ""
    return lo.server_callbacks.get(name, "")


def _serve_forever(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`await server.serve_forever()`: take peers until the server closes.

    The serve loop is compiled code: the marker parks the task on the
    listen descriptor between peers, and the loop body calls the
    callback with the (reader, writer) pair the accept produced. The
    callback rides the server record, so the loop reads it out of the
    record the runtime owns.

    The loop is the flag-break while: the flag ends the body when the
    server was closed, the way CPython's `Server.close` cancels the
    `serve_forever` future. The marker parks the task between peers, so
    the loop body runs once per peer.
    """
    if not enabled():
        lo.fail(node, "PS2001", "coroutines are not lowered")
    if lo._coro_current() is None:
        lo.fail(node, "PS2002", "awaiting a Server is only lowered inside a coroutine")
    slot = lo._temp("svr")
    srv_t = _node_type(receiver)
    lo.locals[slot] = srv_t
    lo.prelude.append(_asg(slot, receiver, span))
    pinned = _nm(slot, srv_t, span)
    # The stop flag: the flag-break while's own condition.
    stop_slot = lo._temp("svstop")
    lo.locals[stop_slot] = _BOOL
    lo.prelude.append(_asg(stop_slot, _const_bool(False, span), span))
    # The peer: the (reader, writer) pair the accept produced. Both
    # halves are the accepted stream record, the same shape
    # `open_connection` builds.
    pair_slot = lo._temp("acp")
    lo.locals[pair_slot] = _STREAM
    reader_slot = lo._temp("ard")
    lo.locals[reader_slot] = _READER
    writer_slot = lo._temp("awr")
    lo.locals[writer_slot] = _WRITER
    # The frame class is the callback's own: the construction call recorded
    # it under the local the server was bound to, and the serve loop reads
    # it back (the server value is a runtime record and cannot carry the
    # type itself).
    frame_name = _server_frame(lo, receiver)
    frame_t = {"kind": "class", "name": frame_name} if frame_name else _FRAME
    frame_slot = lo._temp("cfr")
    lo.locals[frame_slot] = frame_t
    task_t = {"kind": "class", "name": "Task", "elem": frame_t}

    body: list[dict[str, Any]] = []
    # The accept: the runtime takes the peer or parks the task on the
    # listen descriptor. The park raises CancelledError when the server
    # was closed, the way CPython's `Server.close` cancels the
    # serve_forever future, so the loop ends there.
    marker = _call_builtin(PRIM_MARKER, [pinned], _NONE, span)
    marker["prim_op"] = _ACCEPT_OP
    body.append(_expr_stmt(marker, span))
    # The peer the accept produced.
    body.append(
        _asg(
            pair_slot,
            lo._abi_call("asyncio", "Server.accepted", [pinned], _STREAM, "ps_server_accepted", span),
            span,
        )
    )
    shared = _nm(pair_slot, _STREAM, span)
    body.append(_asg(reader_slot, shared, span))
    body.append(_asg(writer_slot, shared, span))
    # The callback call: the callable pair the server record carries, an
    # indirect env-first call through the pair. The callback is a compiled
    # coroutine, so the pair's impl is the wrapper that allocates the
    # frame: the call answers the frame the loop drives as a task, the way
    # `asyncio.create_task` does. The serve loop never awaits the callback,
    # so the frame runs on its own.
    callback = lo._abi_call(
        "asyncio",
        "Server.callback",
        [pinned],
        {"kind": "func", "params": _CALLBACK_PARAMS, "return": frame_t},
        "ps_server_callback",
        span,
    )
    frame = _value_call(callback, [_nm(reader_slot, _READER, span), _nm(writer_slot, _WRITER, span)], frame_t, span)
    body.append(_asg(frame_slot, frame, span))
    body.append(
        _expr_stmt(
            lo._abi_call("asyncio", "create_task", [_nm(frame_slot, frame_t, span)], task_t, "ps_task_new", span),
            span,
        )
    )
    # The loop: the flag ends it after the park raised the cancellation
    # and the serve loop caught it in the compiled body.
    lo.prelude.append(_while_stmt(_not(_nm(stop_slot, _BOOL, span), span), body, span))
    # The await's value: None, the way CPython's serve_forever answers
    # when the server closes.
    return node_to_dict(_ir.const_node(_ir.P_NONE, "", _ir.t_leaf("none"), _mk_span(span)))
