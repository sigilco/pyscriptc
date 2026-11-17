# SPDX-License-Identifier: Apache-2.0
"""asyncio.open_connection and the StreamReader / StreamWriter pair (issue #1281).

`open_connection(host, port)` connects one TCP socket and answers
CPython's `(reader, writer)` pair: two handles over one runtime record
(`runtime/ps_stream.c`). The reader owns the read buffer, the writer the
out buffer; both handles are opaque pointers, the same shape the socket
handle (#1270) uses, so the emitted IR never sees the layout.

The reads are awaited inside a coroutine: the runtime asks the socket
for what it has, parks the running task on the descriptor when the
socket has none (ps_loop_wait_fd), and the await resumes when the loop
wakes the task. The suspension rides the PRIM marker, the same marker
the asyncio primitives use; the operation key is `stream_read`, the
value the await produces is None, and the bytes come out of the record
through the reader call after the resume.

Everything outside the closed surface fences: `open_connection` takes
exactly two positional arguments (typeshed's optional host and the `ssl`
/ `limit` keywords are outside the subset), the reader's `readuntil`,
`at_eof` and the rest, the writer's `writelines`, `write_eof` and the
`transport` attribute, and `start_server` (the issue's second slice)
name their fence with `PS2001`.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.coro import PRIM_MARKER, enabled
from pyscriptc.restlower._shared import (
    _asg,
    _call_builtin,
    _class,
    _const_int,
    _expr_stmt,
    _nm,
    _node_type,
    _t_leaf,
    _type_kind,
)

# Both handles lower to the one record class: the pair halves differ by
# the method surface, not by the type.
_STREAM = _class("StreamReader")
_WRITER = _class("StreamWriter")
_BYTES = _t_leaf("bytes")
_NONE = _t_leaf("none")

# The PRIM operation the reader's await parks on: the runtime call takes
# the record or parks the running task on the descriptor, and the await
# suspends on the 0.
_READ_OP = "stream_read"


def _endpoint_host(lo: Any, value: ast.expr, what: str) -> dict[str, Any]:
    """The host argument: a str. CPython's `None` spelling (the loop
    default) is outside the closed two-arg form."""
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


def _open_connection(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: Any = None, attr: str = ""
) -> dict[str, Any]:
    """`asyncio.open_connection(host, port)`: connect and build the pair.

    The two handles share the record `ps_stream_open` builds; the pair is
    the materialized (reader, writer) tuple, CPython's answer shape. The
    runtime symbols live in the socket file the mobile archive excludes,
    so the call fences there like `socket.connect` does.
    """
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "asyncio.open_connection expects 2 argument(s)")
    for keyword in node.keywords:
        lo.fail(keyword.value, "PS2003", f"asyncio.open_connection has no keyword argument {keyword.arg}")
    lo.mobile_guard(node, "asyncio.open_connection", "sock")
    host = _endpoint_host(lo, node.args[0], "asyncio.open_connection")
    port = _endpoint_port(lo, node.args[1], "asyncio.open_connection")
    record = lo._abi_call("asyncio", "open_connection", [host, port], _STREAM, "ps_stream_open", span)
    # The two halves are two names over one record: the reader and the
    # writer CPython answers share the socket, so the record call is
    # pinned to one temp and both pins carry that temp (a second call
    # would connect a second socket).
    record_slot = lo._temp("st")
    lo.locals[record_slot] = _STREAM
    lo.prelude.append(_asg(record_slot, record, span))
    shared = _nm(record_slot, _STREAM, span)
    reader_slot = lo._temp("rd")
    lo.locals[reader_slot] = _STREAM
    lo.prelude.append(_asg(reader_slot, shared, span))
    writer_slot = lo._temp("wr")
    lo.locals[writer_slot] = _WRITER
    lo.prelude.append(_asg(writer_slot, shared, span))
    return lo._materialize_tuple([_nm(reader_slot, _STREAM, span), _nm(writer_slot, _WRITER, span)], span, node)


def _stream_read_marker(
    lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any], symbol: str, mode: int, size: Any
) -> dict[str, Any]:
    """The awaited read: the PRIM marker parks the task on the socket.

    The marker suspends the frame while the socket has nothing to read;
    the resume falls through and the bytes call after the marker takes
    what the reader holds. Inside a coroutine the lowering is the marker
    plus the bytes read; outside one it fences (CPython raises
    `RuntimeError` there, the compile-time fence names the rule).

    The request rides in the record: `ps_stream_want` records what the
    read asks for (the mode and the n), and the park answers whether
    the request is satisfiable right now. A readline that sees a
    partial line parks again, the way a short read does.
    """
    if not enabled():
        lo.fail(node, "PS2001", "coroutines are not lowered")
    if lo._coro_current() is None:
        lo.fail(node, "PS2002", "awaiting a StreamReader is only lowered inside a coroutine")
    slot = lo._temp("sr")
    stream_t = _node_type(receiver)
    lo.locals[slot] = stream_t
    lo.prelude.append(_asg(slot, receiver, span))
    pinned = _nm(slot, stream_t, span)
    # The request first: the park reads it out of the record.
    lo.prelude.append(
        _expr_stmt(
            lo._abi_call(
                "asyncio", "StreamReader.want", [pinned, _const_int(mode, span), size], _NONE, "ps_stream_want", span
            ),
            span,
        )
    )
    marker = _call_builtin(PRIM_MARKER, [pinned], _NONE, span)
    marker["prim_op"] = _READ_OP
    lo.prelude.append(_expr_stmt(marker, span))
    if symbol == "ps_stream_readline":
        return lo._abi_call("asyncio", "StreamReader.readline", [pinned], _BYTES, symbol, span)
    if symbol == "ps_stream_readexactly":
        return lo._abi_call("asyncio", "StreamReader.readexactly", [pinned, size], _BYTES, symbol, span)
    return lo._abi_call("asyncio", "StreamReader.read", [pinned, size], _BYTES, symbol, span)


def _reader_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """Method dispatch for StreamReader receivers."""
    assert isinstance(node.func, ast.Attribute)
    attr = node.func.attr

    def call(typ: dict[str, Any], symbol: str, args: list[dict[str, Any]], what: str) -> dict[str, Any]:
        return lo._abi_call("asyncio", what, [receiver, *args], typ, symbol, span)

    if attr == "read":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "StreamReader.read", "sock")
        if len(node.args) > 1:
            lo.fail(node, "PS2003", "StreamReader.read expects at most 1 argument(s)")
        size = _const_int(-1, span)
        mode = 3
        if node.args:
            arg = lo._expr(node.args[0])
            if _type_kind(arg["type"]) != "int":
                lo.fail(node.args[0], "PS1003", "StreamReader.read n is int")
            size = arg
            mode = 0
        return _stream_read_marker(lo, node, span, receiver, "ps_stream_read", mode, size)
    if attr == "readline":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "StreamReader.readline", "sock")
        if node.args:
            lo.fail(node, "PS2003", "StreamReader.readline expects 0 argument(s)")
        return _stream_read_marker(lo, node, span, receiver, "ps_stream_readline", 1, _const_int(0, span))
    if attr == "readexactly":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "StreamReader.readexactly", "sock")
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "StreamReader.readexactly expects 1 argument(s)")
        n = lo._expr(node.args[0])
        if _type_kind(n["type"]) != "int":
            lo.fail(node.args[0], "PS1003", "StreamReader.readexactly n is int")
        if not enabled():
            lo.fail(node, "PS2001", "coroutines are not lowered")
        if lo._coro_current() is None:
            lo.fail(node, "PS2002", "awaiting a StreamReader is only lowered inside a coroutine")
        return _stream_read_marker(lo, node, span, receiver, "ps_stream_readexactly", 2, n)
    lo.fail(node, "PS2001", f"StreamReader.{attr} is not in the lowered subset of asyncio.streams")
    raise AssertionError("unreachable")


def _writer_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """Method dispatch for StreamWriter receivers."""
    assert isinstance(node.func, ast.Attribute)
    attr = node.func.attr

    def call(typ: dict[str, Any], symbol: str, args: list[dict[str, Any]], what: str) -> dict[str, Any]:
        return lo._abi_call("asyncio", what, [receiver, *args], typ, symbol, span)

    if attr == "write":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "StreamWriter.write", "sock")
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "StreamWriter.write expects 1 argument(s)")
        data = lo._expr(node.args[0])
        if _type_kind(data["type"]) not in {"bytes", "str"}:
            lo.fail(node.args[0], "PS1003", "StreamWriter.write data is bytes")
        return call(_NONE, "ps_stream_write_buf", [data], "write")
    if attr == "drain":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "StreamWriter.drain", "sock")
        if node.args:
            lo.fail(node, "PS2003", "StreamWriter.drain expects 0 argument(s)")
        return call(_NONE, "ps_stream_drain", [], "drain")
    if attr == "close":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "StreamWriter.close", "sock")
        if node.args:
            lo.fail(node, "PS2003", "StreamWriter.close expects 0 argument(s)")
        return call(_NONE, "ps_stream_close", [], "close")
    if attr == "wait_closed":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "StreamWriter.wait_closed", "sock")
        if node.args:
            lo.fail(node, "PS2003", "StreamWriter.wait_closed expects 0 argument(s)")
        return call(_NONE, "ps_stream_wait_closed", [], "wait_closed")
    lo.fail(node, "PS2001", f"StreamWriter.{attr} is not in the lowered subset of asyncio.streams")
    raise AssertionError("unreachable")
