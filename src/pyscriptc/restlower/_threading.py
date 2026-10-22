"""threading phase 1: Lock, RLock, get_ident and the current thread.

The design doc (2026-10-21-async-threads-design.md, P2a) scopes phase 1 to
the primitives that need no second thread: the locks are real pthread mutexes
but the compiled program runs on one thread, so the surface is single-thread
correct today and phase 2 (Thread, Condition, Semaphore) reuses the same
records unchanged.

`threading.Lock()` and `threading.RLock()` lower to a value of the opaque
class `Lock`, the same shape `collections.deque` uses: one pointer to a
runtime record, never an emitted struct, so the pthread layout stays a
runtime detail.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import _class, _const_int

_LOCK = _class("Lock")
_INT = {"kind": "int"}
_BOOL = {"kind": "bool"}
_NONE = {"kind": "none"}


def _lock_value(lo: Any, node: ast.Call, span: dict[str, Any], recursive: bool) -> dict[str, Any]:
    """`Lock()` / `RLock()`: allocate the runtime record."""
    flag = _const_int(1 if recursive else 0, span)
    return lo._abi_call("threading", "Lock", [flag], _LOCK, "ps_lock_new", span)


def _threading_call(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """`threading.Lock`, `threading.RLock`, `threading.get_ident`, `threading.active_count`."""
    lo._reject_keywords(node)
    if attr in {"Lock", "RLock"}:
        if node.args:
            lo.fail(node, "PS2003", f"threading.{attr} expects 0 argument(s)")
        return _lock_value(lo, node, span, attr == "RLock")
    if attr == "get_ident":
        if node.args:
            lo.fail(node, "PS2003", "threading.get_ident expects 0 argument(s)")
        return lo._abi_call("threading", "get_ident", [], _INT, "ps_thread_ident", span)
    if attr == "active_count":
        if node.args:
            lo.fail(node, "PS2003", "threading.active_count expects 0 argument(s)")
        return lo._abi_call("threading", "active_count", [], _INT, "ps_thread_active_count", span)
    lo.fail(node, "PS2001", f"threading.{attr} is not in the lowered subset of threading")
    raise AssertionError("unreachable")


def _lock_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`lock.acquire()`, `lock.release()`, `lock.locked()`.

    `acquire` takes the CPython keywords `blocking` and `timeout`. The
    blocking form waits forever; a timeout is in seconds and may be None,
    which CPython reads as "block forever".
    """
    assert isinstance(node.func, ast.Attribute)
    attr = node.func.attr
    if attr == "release":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "Lock.release expects 0 argument(s)")
        return lo._abi_call("threading", "Lock.release", [receiver], _NONE, "ps_lock_release", span)
    if attr == "locked":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "Lock.locked expects 0 argument(s)")
        return lo._abi_call("threading", "Lock.locked", [receiver], _BOOL, "ps_lock_locked", span)
    if attr == "acquire":
        blocking = True
        timeout: dict[str, Any] | None = None
        for keyword in node.keywords:
            if keyword.arg == "blocking":
                blocking = _keyword_bool(lo, keyword.value, "blocking")
            elif keyword.arg == "timeout":
                timeout = _keyword_timeout(lo, keyword.value, span)
            else:
                lo.fail(node, "PS2003", f"Lock.acquire has no keyword argument {keyword.arg}")
        if node.args:
            if len(node.args) > 1:
                lo.fail(node, "PS2003", "Lock.acquire expects at most 1 argument(s)")
            blocking = _keyword_bool(lo, node.args[0], "blocking")
        block = _const_int(1 if blocking else 0, span)
        # A timeout of -1 means "wait forever"; the runtime takes nanoseconds.
        wait = _const_int(-1, span)
        if timeout is not None:
            wait = _timeout_nanos(timeout, span)
        return lo._abi_call("threading", "Lock.acquire", [receiver, block, wait], _BOOL, "ps_lock_acquire", span)
    lo.fail(node, "PS2001", f"Lock.{attr} is not lowered")
    raise AssertionError("unreachable")


def _keyword_bool(lo: Any, value: ast.expr, what: str) -> bool:
    """`blocking` is a constant bool in this subset.

    The blocking flag is folded at lowering time because it selects between
    the blocking and the non-blocking acquire in the runtime.
    """
    arg = lo._expr(value)
    if arg["kind"] == "const" and arg["type"]["kind"] == "bool":
        return bool(arg["value"])
    if arg["kind"] == "const" and arg["type"]["kind"] == "int":
        return int(arg["value"]) != 0
    lo.fail(value, "PS1003", f"Lock.acquire {what} is a constant bool")
    raise AssertionError("unreachable")


def _keyword_timeout(lo: Any, value: ast.expr, span: dict[str, Any]) -> dict[str, Any]:
    arg = lo._expr(value)
    if arg["type"]["kind"] == "none":
        return {"kind": "none"}
    if arg["type"]["kind"] == "float":
        return arg
    if arg["type"]["kind"] == "int":
        # The multiply below is float, so widen the int first.
        return {"kind": "cast", "op": "int_to_float", "value": arg, "type": {"kind": "float"}, "span": span}
    lo.fail(value, "PS1003", "Lock.acquire timeout is a number or None")
    raise AssertionError("unreachable")


def _timeout_nanos(timeout: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    """Seconds to nanoseconds, as an int expression."""
    if timeout["kind"] == "none":
        return _const_int(-1, span)
    # float seconds: int(timeout * 1e9). 1e9 as a float const keeps the
    # multiply float-typed without a cast node for the literal.
    billion = {"kind": "const", "value": "1000000000.0", "type": {"kind": "float"}, "span": span}
    return {
        "kind": "call",
        "callee": {"kind": "builtin", "name": "int"},
        "args": [
            {
                "kind": "binary",
                "op": "*",
                "left": timeout,
                "right": billion,
                "type": {"kind": "float"},
                "span": span,
            }
        ],
        "type": _INT,
        "span": span,
    }
