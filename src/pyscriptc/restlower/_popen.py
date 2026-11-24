"""subprocess.Popen lowering: the synchronous child handle (issue #1345).

`Popen(argv)` forks and execs the argv list and hands back the handle at
once; the spawn does not wait. `proc.pid` reads the child's pid,
`proc.returncode` the reaped code (-1 while the child still runs, the
closed-subset spelling of CPython's None), `poll()` reaps without
blocking, `wait(timeout=)` bounds the blocking wait and raises
`subprocess.TimeoutExpired` when the bound passes (the child stays
alive, CPython's contract), `terminate()`/`kill()`/`send_signal(sig)`
signal the child, and the piped streams read and write incrementally:
`proc.stdout.read(n)`/`readline()` (and the same pair on `proc.stderr`)
pull from the open descriptors, `proc.stdin.write(data)`/`flush()`
pushes into the child's stdin. `communicate()` is not on this surface:
it would drain both pipes to EOF and block until the child exits, the
shape the interactive caller is here to avoid.

The timeout is a compile-time number literal: the runtime helper takes
the whole bound as an argument, so a computed timeout fences rather
than being ignored (the rule `subprocess.run(timeout=)` already set).
`stdout=`/`stderr=`/`stdin=` name `subprocess.PIPE`; every other spawn
keyword (the environment and the working directory, issue #1344) fences.
The pipe handles are not optional here: `proc.stdout` always answers a
handle, and a read on a stream that was not piped raises ValueError at
run time (CPython answers None there and the read raises on the None).
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import (
    _class,
    _flag,
    _int,
    _node_type,
    _node_type_kind,
    _t_leaf,
    _type_kind,
)

_POPEN = _class("Popen")
_PIPE = _class("PopenPipe")
_BYTES = _t_leaf("bytes")

# The pipe mask bits the runtime ABI carries: bit 0 stdout, bit 1 stderr,
# bit 2 stdin.
_PIPE_STDOUT = 1
_PIPE_STDERR = 2
_PIPE_STDIN = 4

# The spawn keywords the surface reads. Every other Popen keyword (env=,
# cwd=, shell=, start_new_session=, ...) is issue #1344's surface and
# fences here.
_POPEN_KEYWORDS = {"stdout", "stderr", "stdin"}

# The stream selectors the pipe handle carries.
_STREAM_OUT = 0
_STREAM_ERR = 1
_STREAM_IN = 2


def _argv(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """The command list: `list[str]` with one element type."""
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "subprocess.Popen expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    arg_t = _node_type(arg)
    if _type_kind(arg_t) != "list" or _flag(arg_t, "set"):
        lo.fail(node, "PS1003", "subprocess.Popen takes a list of str")
    if _type_kind(arg_t["elem"]) != "str":
        lo.fail(node, "PS1003", "subprocess.Popen takes a list of str")
    return arg


def _pipe_mask(lo: Any, node: ast.Call, span: dict[str, Any]) -> int:
    """`stdout=`/`stderr=`/`stdin=` name `subprocess.PIPE` (-1, the module
    constant): each piped stream dup2s a pipe over itself. Anything else
    fences."""
    mask = 0
    for keyword in node.keywords:
        if keyword.arg not in _POPEN_KEYWORDS:
            continue
        value = keyword.value
        piped = False
        if isinstance(value, ast.Constant) and value.value == -1:
            piped = True
        elif isinstance(value, ast.Attribute) and value.attr == "PIPE":
            piped = True
        if not piped:
            lo.fail(value, "PS2003", f"subprocess.Popen {keyword.arg} takes subprocess.PIPE")
        mask |= {"stdout": _PIPE_STDOUT, "stderr": _PIPE_STDERR, "stdin": _PIPE_STDIN}[keyword.arg]
    return mask


def _popen_new(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`subprocess.Popen(argv)`: the child starts now.

    The spawn returns at once: the handle is the child record, and the
    caller's own statements drive it (poll, wait, the pipe reads).
    """
    lo.mobile_guard(node, "subprocess.Popen", "subprocess")
    for keyword in node.keywords:
        if keyword.arg is None:
            lo.fail(node, "PS2002", "subprocess.Popen takes no **kwargs")
        if keyword.arg not in _POPEN_KEYWORDS:
            lo.fail(
                node,
                "PS2003",
                f"subprocess.Popen keyword {keyword.arg} is not lowered (issue #1344)",
            )
    argv = _argv(lo, node, span)
    mask = _pipe_mask(lo, node, span)
    return lo._abi_call("subprocess", "Popen", [argv, _int(mask, span)], _POPEN, "ps_popen_new", span)


def _timeout_ms(lo: Any, node: ast.Call, span: dict[str, Any]) -> int:
    """The `wait(timeout=)` bound in whole milliseconds, or 0 for no
    deadline. A literal number is a `Constant` (or a negated one);
    everything else fences."""
    for keyword in node.keywords:
        if keyword.arg == "timeout":
            value = keyword.value
            if isinstance(value, ast.Constant) and value.value is None:
                return 0
            seconds = _literal_seconds(value)
            if seconds is None:
                lo.fail(value, "PS2003", "Popen.wait timeout takes a number literal (seconds)")
            if seconds <= 0:
                lo.fail(value, "PS2003", "Popen.wait timeout takes a positive number of seconds")
            return max(1, int(seconds * 1000 + 0.5))
    return 0


def _literal_seconds(value: ast.expr) -> float | None:
    """A literal number of seconds, or None when the value computes."""
    if isinstance(value, ast.Constant):
        if isinstance(value.value, bool) or not isinstance(value.value, (int, float)):
            return None
        return float(value.value)
    if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.USub):
        inner = value.operand
        if (
            isinstance(inner, ast.Constant)
            and isinstance(inner.value, (int, float))
            and not isinstance(inner.value, bool)
        ):
            return -float(inner.value)
    return None


def _read_n(lo: Any, node: ast.Call) -> int:
    """The `read(n)` count: -1 is CPython's read-all.

    The count is a compile-time literal: the runtime helper takes it as
    an argument, so a computed count has no lowering.
    """
    if len(node.args) > 1:
        lo.fail(node, "PS2003", "the pipe read expects 0 or 1 argument(s)")
    if not node.args:
        return -1
    value = node.args[0]
    if not isinstance(value, ast.Constant) or not isinstance(value.value, int) or isinstance(value.value, bool):
        lo.fail(value, "PS2003", "the pipe read n takes an int literal")
    return int(value.value)


def _signal_number(lo: Any, node: ast.expr) -> dict[str, Any]:
    """The signal number: a `signal.SIG*` module constant.

    The constant lowers to the kernel number the host read, so the
    runtime's ABI takes it whole.
    """
    if not (isinstance(node, ast.Attribute) and node.attr.startswith("SIG")):
        lo.fail(node, "PS2003", "Popen.send_signal takes a signal.SIG* constant")
    number = lo._expr(node)
    if _node_type_kind(number) != "int":
        lo.fail(node, "PS2003", "Popen.send_signal takes a signal.SIG* constant")
    return number


def _reaped_fill(lo: Any, obj: dict[str, Any], span: dict[str, Any], node: ast.AST) -> dict[str, Any]:
    """The tagged `int | None` over the child record's reaped state.

    The runtime answers the reaped flag first and the code second, the
    same hit-then-fill shape `os.getenv` and `shutil.which` read: the
    flag says whether the child has been reaped (the tagged None is the
    child that still runs, CPython's exact shape), and the code fills
    the cell only on a hit. `poll()` and the `returncode` read share
    the shape.
    """
    from pyscriptc.lower._call_attr import _if_node, _opt_fill

    opt_t = lo._opt_type(_t_leaf("int"), node)
    res = lo._opt_box(None, opt_t, span)
    dest = res["id"]
    hit = lo._abi_call("subprocess", "Popen.poll.hit", [obj], _t_leaf("bool"), "ps_popen_reaped", span)
    got = lo._abi_call("subprocess", "Popen.poll.get", [obj], _t_leaf("int"), "ps_popen_poll", span)
    lo.prelude.append(_if_node(hit, _opt_fill(dest, opt_t, got, span), [], span))
    return res


def _popen_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """Method dispatch for the Popen receiver."""
    assert isinstance(node.func, ast.Attribute)
    attr = node.func.attr

    def call(what: str, typ: dict[str, Any], symbol: str, args: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return lo._abi_call("subprocess", what, [receiver, *(args or [])], typ, symbol, span)

    if attr == "poll":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "Popen.poll expects 0 argument(s)")
        return _reaped_fill(lo, receiver, span, node)
    if attr == "wait":
        lo.mobile_guard(node, "Popen.wait", "subprocess")
        return call("Popen.wait", _t_leaf("int"), "ps_popen_wait", [_int(_timeout_ms(lo, node, span), span)])
    if attr == "terminate":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "Popen.terminate expects 0 argument(s)")
        return call("Popen.terminate", _t_leaf("none"), "ps_popen_terminate")
    if attr == "kill":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "Popen.kill expects 0 argument(s)")
        return call("Popen.kill", _t_leaf("none"), "ps_popen_kill")
    if attr == "send_signal":
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "Popen.send_signal expects 1 argument(s)")
        number = _signal_number(lo, node.args[0])
        return call("Popen.send_signal", _t_leaf("none"), "ps_popen_signal", [number])
    if attr == "communicate":
        lo.fail(
            node,
            "PS2001",
            "Popen.communicate is not lowered; the incremental reads replace it (issue #1345)",
        )
    lo.fail(node, "PS2001", f"Popen.{attr} is not lowered")
    raise AssertionError("unreachable")


def _pipe_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """Method dispatch for the pipe handle.

    The handle carries the stream selector, so the read/write split
    follows the stream the handle names: stdout and stderr read,
    stdin writes.
    """
    assert isinstance(node.func, ast.Attribute)
    attr = node.func.attr

    def call(what: str, typ: dict[str, Any], symbol: str, args: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return lo._abi_call("subprocess", what, [receiver, *(args or [])], typ, symbol, span)

    if attr == "read":
        lo._reject_keywords(node)
        n = _read_n(lo, node)
        return call("PopenPipe.read", _BYTES, "ps_pipe_read", [_int(n, span)])
    if attr == "readline":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "the pipe readline expects 0 argument(s)")
        return call("PopenPipe.readline", _BYTES, "ps_pipe_out_readline")
    if attr == "write":
        lo._reject_keywords(node)
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "the pipe write expects 1 argument(s)")
        data = lo._expr(node.args[0])
        if _node_type_kind(data) != "bytes":
            lo.fail(node.args[0], "PS1003", "the pipe write takes bytes")
        # The bytes argument reaches the ABI as its length and its
        # pointer, so the runtime helper takes the two.
        return call("PopenPipe.write", _t_leaf("int"), "ps_pipe_write", [data])
    if attr == "flush":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "the pipe flush expects 0 argument(s)")
        return call("PopenPipe.flush", _t_leaf("none"), "ps_pipe_flush")
    if attr == "close":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "the pipe close expects 0 argument(s)")
        return call("PopenPipe.close", _t_leaf("none"), "ps_pipe_close")
    lo.fail(node, "PS2001", f"PopenPipe.{attr} is not lowered")
    raise AssertionError("unreachable")
