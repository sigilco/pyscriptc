# SPDX-License-Identifier: Apache-2.0
"""socket: the blocking TCP client and server subset (issue #1270).

The closed surface is `socket.socket(AF_INET, SOCK_STREAM)`, the loopback
TCP echo shape: `bind((host, port))`, `listen()`, `accept()`,
`connect((host, port))`, `recv(n)`, `send`, `sendall`, `close`,
`setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)` and the
context-manager form. IPv4 literals and named hosts only; `accept()`
answers the (conn fd, "addr:port") pair as the synthetic tuple the
`os.pipe` pair rides.

Everything else fences: `AF_INET6`, `SOCK_DGRAM`, `socketpair`,
`sendto`/`recvfrom` (UDP), `setblocking`/`settimeout` (non-blocking mode),
`makefile`, `getsockname`/`getpeername`, `shutdown` and the `ssl` wrap
(`PS2001` naming the divergence). The family, type and option arguments
must be the module constants spelled as attributes (or their int values):
a computed family fences, the same shape `queue.Queue(maxsize)` uses for
its kind.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import (
    _asg,
    _class,
    _const_int,
    _expr_stmt,
    _nm,
    _node_kind,
    _node_type_kind,
    _node_value,
    _t_leaf,
)

_SOCK = _class("Socket")
_INT = _t_leaf("int")
_NONE = _t_leaf("none")
_BYTES = _t_leaf("bytes")

# The (host, port) argument is CPython's 2-tuple; the subset takes the
# tuple display only, like `re.split`'s maxsplit-free form.
_PAIR_ARGS = 2


def _socket_call(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None, attr: str
) -> dict[str, Any]:
    """`socket.socket(family, type, proto)` and the module constants."""
    if attr != "socket":
        lo.fail(node, "PS2001", f"socket.{attr} is not in the lowered subset of socket")
        raise AssertionError("unreachable")
    lo.mobile_guard(node, "socket.socket", "sock")
    lo._reject_keywords(node)
    if len(node.args) > 3:
        lo.fail(node, "PS2003", "socket.socket expects at most 3 argument(s)")
    family = 2  # AF_INET
    sock_type = 1  # SOCK_STREAM
    if len(node.args) >= 1:
        family = _const_int_arg(lo, node.args[0], "socket.socket family")
    if len(node.args) >= 2:
        sock_type = _const_int_arg(lo, node.args[1], "socket.socket type")
    if len(node.args) >= 3:
        _const_int_arg(lo, node.args[2], "socket.socket proto")
    if family != 2:
        lo.fail(node, "PS2001", "socket.socket is AF_INET only: the subset has no IPv6 story (divergence 77)")
    if sock_type != 1:
        lo.fail(node, "PS2001", "socket.socket is SOCK_STREAM only: UDP has no story (divergence 77)")
    return lo._abi_call("socket", "socket", [], _SOCK, "ps_sock_new", span)


def _const_int_arg(lo: Any, value: ast.expr, what: str) -> int:
    """A constant int argument (the family, the type, the option level).

    The module constants are the spellings a caller writes
    (`socket.AF_INET`), and they are int constants, so the value folds
    here; a computed family fences, the way `queue.Queue(maxsize)` takes
    a constant.
    """
    arg = lo._expr(value)
    if _node_kind(arg) == "const" and _node_type_kind(arg) in {"int", "bool"}:
        return int(_node_value(arg))
    lo.fail(value, "PS1003", f"{what} is a constant int")
    raise AssertionError("unreachable")


def _no_args(lo: Any, node: ast.Call, what: str) -> None:
    if node.args:
        lo.fail(node, "PS2003", f"{what} expects 0 argument(s)")


def _endpoint(lo: Any, arg: ast.expr, what: str, span: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The (host, port) argument: the tuple display only, a const or
    variable str host and an int port."""
    if not isinstance(arg, ast.Tuple) or len(arg.elts) != _PAIR_ARGS:
        lo.fail(arg, "PS1003", f"{what} takes a (host, port) tuple")
    host = lo._expr(arg.elts[0])
    port = lo._expr(arg.elts[1])
    if _node_type_kind(host) != "str":
        lo.fail(arg.elts[0], "PS1003", f"{what} host is str")
    if _node_type_kind(port) != "int":
        lo.fail(arg.elts[1], "PS1003", f"{what} port is int")
    return host, port


def _socket_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """Method dispatch for Socket receivers."""
    assert isinstance(node.func, ast.Attribute)
    attr = node.func.attr

    def call(typ: dict[str, Any], symbol: str, args: list[dict[str, Any]], what: str) -> dict[str, Any]:
        return lo._abi_call("socket", what, [receiver, *args], typ, symbol, span)

    if attr == "bind":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "socket.bind", "sock")
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "socket.bind expects 1 argument(s)")
        host, port = _endpoint(lo, node.args[0], "bind", span)
        return call(_NONE, "ps_sock_bind", [host, port], "bind")
    if attr == "connect":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "socket.connect", "sock")
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "socket.connect expects 1 argument(s)")
        host, port = _endpoint(lo, node.args[0], "connect", span)
        return call(_NONE, "ps_sock_connect", [host, port], "connect")
    if attr == "listen":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "socket.listen", "sock")
        if len(node.args) > 1:
            lo.fail(node, "PS2003", "socket.listen expects at most 1 argument(s)")
        backlog = _const_int(-1, span)
        if node.args:
            arg = lo._expr(node.args[0])
            if _node_type_kind(arg) != "int":
                lo.fail(node.args[0], "PS1003", "socket.listen backlog is int")
            backlog = arg
        return call(_NONE, "ps_sock_listen", [backlog], "listen")
    if attr == "accept":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "socket.accept", "sock")
        _no_args(lo, node, "socket.accept")
        # CPython's accept answers the (conn, address) pair: the conn half
        # is a socket, the address half the (host, port) pair on IPv4. The
        # address rides the listening socket's record, so the peer read
        # comes after the accept that filled it: the accept call is bound
        # to a temp first, and the peer read rides the same order.
        conn_box = lo._temp("conn")
        lo.locals[conn_box] = _SOCK
        lo.prelude.append(
            _asg(conn_box, lo._abi_call("socket", "accept", [receiver], _SOCK, "ps_sock_accept", span), span)
        )
        conn = _nm(conn_box, _SOCK, span)
        host = lo._abi_call("socket", "peer_host", [receiver], _t_leaf("str"), "ps_sock_peer_host", span)
        port = lo._abi_call("socket", "peer_port", [receiver], _INT, "ps_sock_peer_port", span)
        addr = lo._materialize_tuple([host, port], span, node)
        return lo._materialize_tuple([conn, addr], span, node)
    if attr == "recv":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "socket.recv", "sock")
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "socket.recv expects 1 argument(s)")
        size = lo._expr(node.args[0])
        if _node_type_kind(size) != "int":
            lo.fail(node.args[0], "PS1003", "socket.recv bufsize is int")
        return call(_BYTES, "ps_sock_recv", [size], "recv")
    if attr == "send":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "socket.send", "sock")
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "socket.send expects 1 argument(s)")
        data = lo._expr(node.args[0])
        if _node_type_kind(data) not in {"bytes", "str"}:
            lo.fail(node.args[0], "PS1003", "socket.send data is bytes")
        return call(_INT, "ps_sock_send", [data], "send")
    if attr == "sendall":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "socket.sendall", "sock")
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "socket.sendall expects 1 argument(s)")
        data = lo._expr(node.args[0])
        if _node_type_kind(data) not in {"bytes", "str"}:
            lo.fail(node.args[0], "PS1003", "socket.sendall data is bytes")
        return call(_NONE, "ps_sock_sendall", [data], "sendall")
    if attr == "setsockopt":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "socket.setsockopt", "sock")
        if len(node.args) != 3:
            lo.fail(node, "PS2003", "socket.setsockopt expects 3 argument(s)")
        for index, what in ((0, "level"), (1, "optname")):
            arg = lo._expr(node.args[index])
            if _node_type_kind(arg) != "int":
                lo.fail(node.args[index], "PS1003", f"socket.setsockopt {what} is an int constant")
        value = lo._expr(node.args[2])
        if _node_type_kind(value) != "int":
            lo.fail(node.args[2], "PS1003", "socket.setsockopt value is an int literal")
        return call(_NONE, "ps_sock_setsockopt_reuseaddr", [], "setsockopt")
    if attr == "close":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "socket.close", "sock")
        _no_args(lo, node, "socket.close")
        return call(_NONE, "ps_sock_close", [], "close")
    if attr == "fileno":
        lo._reject_keywords(node)
        _no_args(lo, node, "socket.fileno")
        return call(_INT, "ps_sock_fileno", [], "fileno")
    if attr == "getsockname":
        lo._reject_keywords(node)
        lo.mobile_guard(node, "socket.getsockname", "sock")
        _no_args(lo, node, "socket.getsockname")
        # CPython's getsockname answers the (host, port) pair of the bound
        # address; port 0 lets the kernel pick, so a program reads the
        # assigned port back here (the probe and the tests bind 0).
        lo.prelude.append(
            _expr_stmt(lo._abi_call("socket", "getsockname", [receiver], _NONE, "ps_sock_getsockname", span), span)
        )
        host = lo._abi_call("socket", "peer_host", [receiver], _t_leaf("str"), "ps_sock_peer_host", span)
        port = lo._abi_call("socket", "peer_port", [receiver], _INT, "ps_sock_peer_port", span)
        return lo._materialize_tuple([host, port], span, node)
    if attr in {"sendto", "recvfrom"}:
        lo.fail(node, "PS2001", f"socket.{attr} is UDP, which the subset does not lower (divergence 77)")
    if attr in {"setblocking", "settimeout", "gettimeout"}:
        lo.fail(node, "PS2001", f"socket.{attr} is non-blocking mode, which the subset does not lower (divergence 77)")
    lo.fail(node, "PS2001", f"socket.{attr} is not in the lowered subset of socket")
    raise AssertionError("unreachable")
