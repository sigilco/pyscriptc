# SPDX-License-Identifier: Apache-2.0
"""asyncio.create_subprocess_exec over the sync subprocess spawn.

The call takes the program and its arguments as positional str (the shape
CPython's takes) and hands back an `AsyncProcess` record: the runtime owns
the child, the loop's pump reaps it.

`await proc.wait()` parks the task until the child exits and reads the exit
code; the marker the coro pass expands is the park. `proc.returncode` reads
the same code, the way CPython's does.

The subset is closed: `stdout=`/`stderr=` pipes, `env=`, `cwd=` and
`shell=` are not lowered, so a call that asks for them fences instead of
silently dropping the request.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.coro import PROC_MARKER, _await_result_name, enabled
from pyscriptc.restlower._shared import (
    _asg,
    _call_builtin,
    _class,
    _expr_stmt,
    _int,
    _list_lit,
    _nm,
    _node_type,
    _t_leaf,
    _type_kind,
)
from pyscriptc.stdmore import module_constant

# The process record: the runtime owns the child, so the value is the
# record pointer (the shape the asyncio primitives use).
_PROCESS = _class("AsyncProcess")

# The capture keywords the subset reads (`stdout=`, `stderr=`, issue #1266),
# and the pipe flag bit each one sets in the runtime's pipe mask: bit 0
# stdout, bit 1 stderr.
_PIPE_KEYWORDS = {"stdout": 1, "stderr": 2}


def _argv(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """The command: the positional args are the argv list, one str each."""
    if not node.args:
        lo.fail(node, "PS2003", "asyncio.create_subprocess_exec expects at least 1 argument(s)")
    elems: list[dict[str, Any]] = []
    for arg_node in node.args:
        arg = lo._expr(arg_node)
        if _type_kind(_node_type(arg)) != "str":
            lo.fail(arg_node, "PS1003", "asyncio.create_subprocess_exec takes str arguments")
        elems.append(arg)
    return _list_lit(elems, _t_leaf("str"), span)


def _pipe_mask(lo: Any, node: ast.Call, span: dict[str, Any]) -> int:
    """The pipe mask the capture keywords spell (`stdout=`, `stderr=`).

    The value must be `asyncio.subprocess.PIPE` (the module constant -1,
    the number CPython uses): the lowered shape pipes the stream or
    inherits it, so `None` and a file object stay outside the subset and
    fence with PS2003 rather than being silently dropped (issue #1266).
    """
    mask = 0
    for keyword in node.keywords:
        bit = _PIPE_KEYWORDS.get(keyword.arg or "")
        if bit is None:
            continue
        if isinstance(keyword.value, ast.Attribute):
            resolved = lo._resolve_module(keyword.value)
            if resolved is not None:
                dotted, module, member, status = resolved
                if status == "lowered" and module == "asyncio.subprocess" and member == "PIPE":
                    if module_constant(module, member) is not None:
                        mask |= bit
                        continue
                if status == "lowered" and module == "subprocess" and member == "PIPE":
                    lo.fail(
                        keyword.value,
                        "PS2003",
                        "asyncio.create_subprocess_exec takes asyncio.subprocess.PIPE, not subprocess.PIPE",
                    )
        lo.fail(
            keyword.value,
            "PS2003",
            f"asyncio.create_subprocess_exec {keyword.arg} takes asyncio.subprocess.PIPE",
        )
    return mask


def _create_subprocess_exec(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: Any = None, attr: str = ""
) -> dict[str, Any]:
    """`asyncio.create_subprocess_exec(program, *args)`: the child starts now.

    The program and its arguments are the argv list, one str each: the same
    positional form CPython takes. The call is an awaitable: CPython's
    returns a coroutine, so the await parks the task on the spawn the way
    the primitives park. The spawn itself does not block, so the park is
    short: the runtime hands the record back and the frame resumes with it.

    `stdout=`/`stderr=` name `asyncio.subprocess.PIPE` (issue #1266): the
    spawn dup2s a pipe over that stream and the record keeps the read end
    for communicate. Everything else fences.
    """
    lo.mobile_guard(node, "asyncio.create_subprocess_exec", "subprocess")
    pipe_mask = _pipe_mask(lo, node, span)
    for keyword in node.keywords:
        if keyword.arg is None:
            lo.fail(node, "PS2002", "asyncio.create_subprocess_exec takes no keyword arguments")
        if keyword.arg not in _PIPE_KEYWORDS:
            lo.fail(
                node,
                "PS2003",
                f"asyncio.create_subprocess_exec keyword {keyword.arg} is not lowered",
            )
    argv = _argv(lo, node, span)
    return lo._abi_call(
        "asyncio",
        "create_subprocess_exec",
        [argv, _int(pipe_mask, span)],
        _PROCESS,
        "ps_aproc_new",
        span,
    )


def _proc_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """Method dispatch for the async process: `wait()` is the awaitable."""
    assert isinstance(node.func, ast.Attribute)
    attr = node.func.attr
    if attr == "wait":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "Process.wait expects 0 argument(s)")
        return _await_proc_wait(lo, node, span, receiver)
    if attr == "communicate":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "Process.communicate expects 0 argument(s)")
        return _await_proc_communicate(lo, node, span, receiver)
    lo.fail(node, "PS2001", f"Process.{attr} is not lowered")
    raise AssertionError("unreachable")


def _await_proc_communicate(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`await proc.communicate()`: the child's stdout and stderr.

    The call reads both pipes to EOF and reaps the child, the way
    CPython's communicate reads the streams and waits. The result is the
    (stdout, stderr) pair of bytes: the same pair CPython hands back, with
    an inherited stream reading as the empty bytes (CPython answers None
    there, but the pair the subset lowers is one type, and `.decode()` and
    the equality the probes run read the empty bytes the same way).

    The read is a plain runtime call: the child's pipes are its only
    output, so the read runs to EOF with no loop work to interleave. The
    await reads straight through it, the shape `asyncio.gather` awaits.
    """
    if not enabled():
        lo.fail(node, "PS2001", "coroutines are not lowered")
    if lo._coro_current() is None:
        lo.fail(node, "PS2002", "awaiting a process is only lowered inside a coroutine")
    lo.mobile_guard(node, "asyncio.subprocess.Process.communicate", "subprocess")
    out_read = lo._abi_call(
        "asyncio",
        "Process.communicate.stdout",
        [receiver],
        _t_leaf("bytes"),
        "ps_aproc_stdout",
        span,
    )
    err_read = lo._abi_call(
        "asyncio",
        "Process.communicate.stderr",
        [receiver],
        _t_leaf("bytes"),
        "ps_aproc_stderr",
        span,
    )
    return lo._materialize_tuple([out_read, err_read], span, node)


def _await_proc_wait(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`await proc.wait()`: the exit code of the child.

    The wait parks the task: the runtime polls the child, and the loop's
    pump wakes the task when the child exits. The exit code rides out of
    the record on the resume, the shape the queue's take uses.
    """
    if not enabled():
        lo.fail(node, "PS2001", "coroutines are not lowered")
    if lo._coro_current() is None:
        lo.fail(node, "PS2002", "awaiting a process is only lowered inside a coroutine")
    typ = _node_type(receiver)
    slot = lo._temp("prc")
    lo.locals[slot] = typ
    lo.prelude.append(_asg(slot, receiver, span))
    marker = _call_builtin(PROC_MARKER, [_nm(slot, typ, span)], _t_leaf("none"), span)
    lo.prelude.append(_expr_stmt(marker, span))
    result = _await_result_name(slot)
    lo.locals[result] = _t_leaf("int")
    return _nm(result, _t_leaf("int"), span)
