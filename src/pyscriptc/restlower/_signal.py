# SPDX-License-Identifier: MIT
"""signal call lowering: the constants and the native handler surface.

The `signal` subset carries the POSIX constants the watchdog needs
(`SIGKILL`, `SIGTERM`, `SIGINT`, `SIGCONT`, `SIGUSR1`, `SIGUSR2`); they lower
as module constants through the `_INT_SURFACE` rows, and `os.kill`/`os.killpg`
take them as plain ints.

`signal.signal(signum, handler)` lowers the native handler surface (issue
#1347). The handler is a compiled `def handler(signum: int) -> None`: the
runtime installs a C-level `sigaction` handler that only records the signal
number, and the compiled handler runs at a safepoint, outside the signal
context. That is CPython's deferred-delivery model in shape: CPython's C
handler sets a flag too and the Python-level handler runs from the eval loop,
not from the signal context.

`signal.SIG_IGN` and `signal.SIG_DFL` install the ignore and default
dispositions. The rest of the registration API (`signal.getsignal`,
`signal.alarm`, the `Signals` enum and `signal.raise_signal`) stays outside
the surface: a native binary has no CPython-level handler table to read back
from, so those forms fence with PS2001 naming the boundary.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import _node_type, _node_type_kind, _t_leaf

# The handler-registration and process-signal forms the subset refuses.
# The message names the boundary: the constants lower, the handler table does
# not exist in a native binary.
_FENCED_CALLS: dict[str, str] = {
    "getsignal": "signal.getsignal reads the Python handler table; a native binary has no handler table",
    "alarm": "signal.alarm needs the SIGALRM handler path; a native binary has no handler table",
    "set_wakeup_fd": "signal.set_wakeup_fd needs the handler plumbing; a native binary has no handler table",
    "pthread_sigmask": "signal.pthread_sigmask is outside the closed signal subset",
    "sigpending": "signal.sigpending is outside the closed signal subset",
    "raise_signal": "signal.raise_signal is outside the closed signal subset; use os.kill(os.getpid(), sig)",
    "InterruptedError": "signal.InterruptedError is a class, not a value in this subset",
    "ItimerError": "signal.ItimerError is a class, not a value in this subset",
    "Signals": "the signal.Signals enum is outside the closed signal subset; use the SIG* module constants",
    "SIGHUP": "signal.SIGHUP is outside the closed signal subset",
    "SIGQUIT": "signal.SIGQUIT is outside the closed signal subset",
    "SIGTRAP": "signal.SIGTRAP is outside the closed signal subset",
    "SIGABRT": "signal.SIGABRT is outside the closed signal subset",
    "SIGBUS": "signal.SIGBUS is outside the closed signal subset",
    "SIGFPE": "signal.SIGFPE is outside the closed signal subset",
    "SIGSEGV": "signal.SIGSEGV is outside the closed signal subset",
    "SIGPIPE": "signal.SIGPIPE is outside the closed signal subset",
    "SIGALRM": "signal.SIGALRM is outside the closed signal subset",
    "SIGCHLD": "signal.SIGCHLD is outside the closed signal subset",
    "SIGTSTP": "signal.SIGTSTP is outside the closed signal subset",
    "SIGTTIN": "signal.SIGTTIN is outside the closed signal subset",
    "SIGTTOU": "signal.SIGTTOU is outside the closed signal subset",
}

_FENCED_METHODS: dict[str, str] = {
    "StrSignal": "the signal.StrSignal mixin is outside the closed signal subset",
    "valid_signal": "signal.valid_signal is outside the closed signal subset",
}

# The two dispositions a native binary can install without a compiled
# handler. CPython spells them `Handlers.SIG_DFL` (0) and `Handlers.SIG_IGN`
# (1); the runtime takes them as distinct calls so the disposition does not
# collide with a signal number.
_IGNORE = "SIG_IGN"
_DEFAULT = "SIG_DFL"


def _signal_call(lo: Any, node: Any, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """`signal.signal(signum, handler)`: the native handler surface."""
    if attr == "signal":
        return _signal_install(lo, node, span)
    message = _FENCED_CALLS.get(attr)
    if message is not None:
        lo.fail(node, "PS2001", message)
    lo.fail(node, "PS2001", f"signal.{attr} is not in the lowered subset of signal")
    raise AssertionError("unreachable")


def _signal_attr(lo: Any, node: Any, dotted: str, member: str) -> None:
    """The bare-attribute reads the fixed call table does not cover."""
    message = _FENCED_CALLS.get(member) or _FENCED_METHODS.get(member)
    if message is not None:
        lo.fail(node, "PS2001", message)
    lo.fail(node, "PS2001", f"{dotted} is not in the lowered subset of signal")
    raise AssertionError("unreachable")


def _disposition(node: ast.expr) -> str | None:
    """`SIG_IGN` or `SIG_DFL` when the expression spells one, else None.

    The spelling matters, not the value: `SIG_IGN` is 1 in CPython and 1 is
    also `SIGHUP` on POSIX, so the int alone cannot name the disposition.
    """
    if not isinstance(node, ast.Attribute):
        return None
    if node.attr == _IGNORE:
        return _IGNORE
    if node.attr == _DEFAULT:
        return _DEFAULT
    return None


def _signal_install(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`signal.signal(signum, handler)`.

    `signum` is an int constant (the `SIG*` module constants or a literal).
    `handler` is a compiled `def handler(signum: int) -> None`, or the
    `SIG_IGN`/`SIG_DFL` disposition.
    """
    lo._reject_keywords(node)
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "signal.signal expects 2 argument(s)")
    signum = lo._expr(node.args[0])
    if _node_type_kind(signum) != "int":
        lo.fail(node.args[0], "PS1003", "signal.signal takes an int signal number")
    disposition = _disposition(node.args[1])
    if disposition is not None:
        symbol = "ps_signal_ignore" if disposition == _IGNORE else "ps_signal_default"
        return lo._abi_call("signal", "signal", [signum], _t_leaf("none"), symbol, span)
    handler = lo._expr(node.args[1])
    if _node_type_kind(handler) != "func":
        lo.fail(node.args[1], "PS1003", "signal.signal takes a callable handler or SIG_IGN/SIG_DFL")
    ftype = _node_type(handler)
    params = ftype.get("params", [])
    if not params or params[0].get("kind") != "int":
        lo.fail(node.args[1], "PS1003", "signal.signal takes a handler with an int signal-number parameter")
    if ftype.get("return", {}).get("kind") != "none":
        lo.fail(node.args[1], "PS1003", "signal.signal takes a handler that returns None")
    return lo._abi_call("signal", "signal", [signum, handler], _t_leaf("none"), "ps_signal_install", span)


__all__ = ["_signal_call", "_signal_attr"]
