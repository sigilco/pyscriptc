# SPDX-License-Identifier: MIT
"""signal call lowering: the constants-only surface (issue #1244).

The closed `signal` subset carries the POSIX constants the watchdog needs
(`SIGKILL`, `SIGTERM`, `SIGINT`, `SIGCONT`, `SIGUSR1`, `SIGUSR2`); they lower
as module constants through the `_INT_SURFACE` rows, and `os.kill`/`os.killpg`
take them as plain ints. The handler-registration API (`signal.signal`,
`signal.getsignal`, `signal.alarm`, the `Signals` enum and `signal.raise_signal`)
stays outside the surface: a native binary has no CPython-level handler table,
so every registration form fences with PS2001 naming the boundary.
"""

from __future__ import annotations

from typing import Any

# The handler-registration and process-signal forms the subset refuses.
# The message names the boundary: the constants lower, the handler table does
# not exist in a native binary.
_FENCED_CALLS: dict[str, str] = {
    "signal": "signal.signal registers a Python handler; a native binary has no handler table",
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
    "SIG_DFL": "signal.SIG_DFL is a handler value; the subset registers no handlers",
    "SIG_IGN": "signal.SIG_IGN is a handler value; the subset registers no handlers",
}

_FENCED_METHODS: dict[str, str] = {
    "StrSignal": "the signal.StrSignal mixin is outside the closed signal subset",
    "valid_signal": "signal.valid_signal is outside the closed signal subset",
}


def _signal_call(lo: Any, node: Any, span: dict[str, Any], attr: str) -> dict[str, Any]:
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
