"""threading phases 1 and 2: locks, threads and the wait primitives.

The design doc (2026-10-21-async-threads-design.md, P2a and P2b) scopes the
surface. Phase 1 (Lock, RLock, get_ident) is single-thread-correct; phase 2
adds `Thread`, `Event`, `Condition` and `Semaphore` on real pthreads. Every
value is an opaque pointer to a runtime record, the same shape
`collections.deque` uses: the pthread layouts stay runtime details.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import _class, _const_int

_LOCK = _class("Lock")
_INT = {"kind": "int"}
_BOOL = {"kind": "bool"}
_NONE = {"kind": "none"}
_THREAD = _class("Thread")
_EVENT = _class("Event")
_COND = _class("Condition")
_SEM = _class("Semaphore")


def _lock_value(lo: Any, node: ast.Call, span: dict[str, Any], recursive: bool) -> dict[str, Any]:
    """`Lock()` / `RLock()`: allocate the runtime record."""
    flag = _const_int(1 if recursive else 0, span)
    return lo._abi_call("threading", "Lock", [flag], _LOCK, "ps_lock_new", span)


def _threading_call(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """`threading.Lock`, `RLock`, `get_ident`, `active_count`, `Event`,
    `Condition`, `Semaphore` and `Thread`."""
    if attr in {"Lock", "RLock"}:
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", f"threading.{attr} expects 0 argument(s)")
        return _lock_value(lo, node, span, attr == "RLock")
    if attr == "get_ident":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "threading.get_ident expects 0 argument(s)")
        return lo._abi_call("threading", "get_ident", [], _INT, "ps_thread_ident", span)
    if attr == "active_count":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "threading.active_count expects 0 argument(s)")
        return lo._abi_call("threading", "active_count", [], _INT, "ps_thread_active_count", span)
    if attr == "Event":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "threading.Event expects 0 argument(s)")
        return lo._abi_call("threading", "Event", [], _EVENT, "ps_event_new", span)
    if attr == "Condition":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "threading.Condition expects 0 argument(s)")
        return _cond_value(lo, node, span)
    if attr == "Semaphore":
        return _semaphore_value(lo, node, span)
    if attr == "Thread":
        return _thread_value(lo, node, span)
    lo.fail(node, "PS2001", f"threading.{attr} is not in the lowered subset of threading")
    raise AssertionError("unreachable")


def _cond_value(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`Condition()`: a class-shaped record initialized by `ps_cond_init`."""
    return {
        "kind": "call",
        "callee": {"kind": "module", "module": "threading", "name": "Condition"},
        "args": [],
        "type": _COND,
        "symbol": "ps_cond_init",
        "span": span,
    }


def _semaphore_value(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`Semaphore(value=1)`: the initial count folds at lowering, default 1."""
    value = 1
    for keyword in node.keywords:
        if keyword.arg != "value":
            lo.fail(node, "PS2003", f"threading.Semaphore has no keyword argument {keyword.arg}")
        value = _const_number(lo, keyword.value, "threading.Semaphore value")
    if node.args:
        if len(node.args) > 1:
            lo.fail(node, "PS2003", "threading.Semaphore expects at most 1 argument(s)")
        value = _const_number(lo, node.args[0], "threading.Semaphore value")
    return lo._abi_call("threading", "Semaphore", [_const_int(value, span)], _SEM, "ps_sem_new", span)


def _thread_value(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`Thread(target=f, args=(...))`: pin target, args and daemon at lowering.

    `Thread(target=f, args=(a, b))` binds the arguments into the callable's
    env, the same way `functools.partial` does: one synthesized env-first stub
    calls the target pair with the bound values prepended. `name` is a
    constant str, `daemon` a constant bool. Positional spellings are accepted
    in the CPython order (group, target, name, args, kwargs) but only
    target/args/daemon bind.
    """
    target: dict[str, Any] | None = None
    args_node: ast.expr | None = None
    daemon = False
    name_value: str | None = None
    params = ["group", "target", "name", "args", "kwargs", "daemon"]
    slots: list[ast.expr | None] = [None] * 6
    for index, arg in enumerate(node.args):
        if index >= 5:
            lo.fail(node, "PS2003", "threading.Thread expects at most 5 argument(s)")
        # daemon has no positional spelling in CPython's Thread.__init__.
        slots[index] = arg
    seen: set[str] = set()
    for keyword in node.keywords:
        assert keyword.arg is not None
        if keyword.arg not in params:
            lo.fail(node, "PS2003", f"threading.Thread has no keyword argument {keyword.arg}")
        if keyword.arg in seen or slots[params.index(keyword.arg)] is not None:
            lo.fail(node, "PS2003", f"threading.Thread argument {keyword.arg} is given more than once")
        seen.add(keyword.arg)
        slots[params.index(keyword.arg)] = keyword.value
    if slots[0] is not None:
        lo.fail(slots[0], "PS2002", "threading.Thread ignores group")
    if slots[5] is not None:
        arg = lo._expr(slots[5])
        if arg["kind"] == "const" and arg["type"]["kind"] in {"bool", "int"}:
            daemon = bool(arg["value"])
        else:
            lo.fail(slots[5], "PS1003", "threading.Thread daemon is a constant bool")
    if slots[4] is not None:
        lo.fail(slots[4], "PS2002", "threading.Thread kwargs are not lowered")
    if slots[1] is not None:
        target = lo._expr(slots[1])
        if target["type"]["kind"] != "func":
            lo.fail(slots[1], "PS1003", "threading.Thread target is a callable")
    if slots[2] is not None:
        arg = lo._expr(slots[2])
        if arg["kind"] == "const" and arg["type"]["kind"] == "str":
            name_value = str(arg["value"])
        else:
            lo.fail(slots[2], "PS1003", "threading.Thread name is a constant str")
    if slots[3] is not None:
        args_node = slots[3]
    if target is None:
        lo.fail(node, "PS1003", "threading.Thread needs a target")
    assert target is not None
    if args_node is None:
        lo.fail(node, "PS2002", "threading.Thread args needs a tuple, even when it is empty")
    assert args_node is not None
    if not isinstance(args_node, ast.Tuple):
        lo.fail(args_node, "PS2002", "threading.Thread args is a tuple")
    if any(isinstance(elt, ast.Starred) for elt in args_node.elts):
        lo.fail(args_node, "PS2002", "threading.Thread args does not take star unpacking")
    if len(args_node.elts) > 3:
        lo.fail(args_node, "PS2002", "threading.Thread args takes at most 3 arguments")
    fields = [lo._expr(elt) for elt in args_node.elts]
    if target["type"]["kind"] == "func":
        want = target["type"]["params"]
        if len(want) != len(fields):
            lo.fail(args_node, "PS2003", f"threading.Thread target expects {len(want)} argument(s)")
        for field, expect in zip(fields, want, strict=True):
            if field["type"]["kind"] != expect["kind"]:
                lo.fail(args_node, "PS1003", "threading.Thread arg has the wrong type for the target")
    if name_value is None:
        name_value = "Thread"
    # The bound arguments ride in the callable's env, the same way
    # `functools.partial` binds them: the trampoline calls the pair with the
    # env, and the synthesized stub prepends the bound values.
    bound = target["type"]["params"][: len(fields)]
    env: list[dict[str, Any]] = [{"init": field, "type": field["type"]} for field in fields]
    env.append({"init": target, "type": target["type"]})
    call = {
        "kind": "call",
        "callee": {"kind": "module", "module": "threading", "name": "Thread"},
        "args": [],
        "type": _THREAD,
        "symbol": "ps_thread_new",
        "span": span,
    }
    call["thread"] = {
        "daemon": 1 if daemon else 0,
        "name": name_value,
        "closure": {
            "kind": "closure",
            "name": "ps.thread",
            "env": env,
            "type": {
                "kind": "func",
                "params": [],
                "return": target["type"]["return"],
            },
            "stub": {
                "bound": bound,
                "params": [],
                "return": target["type"]["return"],
            },
            "span": span,
        },
    }
    return call


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


def _const_number(lo: Any, value: ast.expr, what: str) -> int | None:
    """A constant int, including the unary-minus spelling."""
    arg = lo._expr(value)
    if arg["kind"] == "const" and arg["type"]["kind"] in {"int", "bool"}:
        return int(arg["value"])
    if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.USub):
        inner = lo._expr(value.operand)
        if inner["kind"] == "const" and inner["type"]["kind"] == "int":
            return -int(inner["value"])
    lo.fail(value, "PS1003", f"{what} is a constant int")
    raise AssertionError("unreachable")


def _no_args(lo: Any, node: ast.Call, what: str) -> None:
    """A method that takes no positional argument and no keyword."""
    if node.args:
        lo.fail(node, "PS2003", f"{what} expects 0 argument(s)")


def _timeout_seconds(lo: Any, node: ast.Call, index: int, span: dict[str, Any]) -> dict[str, Any]:
    """A positional timeout argument in seconds, or the -1 forever marker."""
    if len(node.args) <= index:
        return _const_int(-1, span)
    return _timeout_nanos(lo._expr(node.args[index]), span)


def _wait_call(
    lo: Any,
    node: ast.Call,
    span: dict[str, Any],
    receiver: dict[str, Any],
    symbol: str,
    what: str,
) -> dict[str, Any]:
    """`wait()` on Event or Condition: `timeout` in seconds, None blocks.

    The timeout accepts the CPython keyword spelling or the positional one.
    """
    timeout = None
    for keyword in node.keywords:
        if keyword.arg != "timeout":
            lo.fail(node, "PS2003", f"{what} has no keyword argument {keyword.arg}")
        timeout = keyword.value
    if node.args:
        if len(node.args) > 1:
            lo.fail(node, "PS2003", f"{what} expects at most 1 argument(s)")
        timeout = node.args[0]
    if timeout is None:
        wait = _const_int(-1, span)
    else:
        wait = _timeout_nanos(lo._expr(timeout), span)
    return lo._abi_call("threading", what, [receiver, wait], _BOOL, symbol, span)


def _thread_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """Method dispatch for Thread, Event, Condition and Semaphore receivers."""
    assert isinstance(node.func, ast.Attribute)
    attr = node.func.attr
    cls = receiver["type"]["name"]

    def call(typ: dict[str, Any], symbol: str, args: list[dict[str, Any]], what: str) -> dict[str, Any]:
        return lo._abi_call("threading", what, [receiver, *args], typ, symbol, span)

    if cls == "Thread":
        if attr == "start":
            lo._reject_keywords(node)
            _no_args(lo, node, "Thread.start")
            return call(_NONE, "ps_thread_start2", [], "Thread.start")
        if attr == "join":
            timeout = None
            for keyword in node.keywords:
                if keyword.arg != "timeout":
                    lo.fail(node, "PS2003", f"Thread.join has no keyword argument {keyword.arg}")
                timeout = keyword.value
            if node.args:
                if len(node.args) > 1:
                    lo.fail(node, "PS2003", "Thread.join expects at most 1 argument(s)")
                timeout = node.args[0]
            if timeout is None:
                wait = _const_int(-1, span)
            else:
                wait = _timeout_nanos(lo._expr(timeout), span)
            return call(_NONE, "ps_thread_join2", [wait], "Thread.join")
        if attr == "is_alive":
            lo._reject_keywords(node)
            _no_args(lo, node, "Thread.is_alive")
            return call(_BOOL, "ps_thread_is_alive2", [], "Thread.is_alive")
        if attr == "daemon":
            lo._reject_keywords(node)
            _no_args(lo, node, "Thread.daemon")
            return call(_BOOL, "ps_thread_daemon", [], "Thread.daemon")
        if attr == "name":
            lo._reject_keywords(node)
            _no_args(lo, node, "Thread.name")
            return call({"kind": "str"}, "ps_thread_name2", [], "Thread.name")
        lo.fail(node, "PS2001", f"Thread.{attr} is not lowered")
    if cls == "Event":
        if attr == "set":
            lo._reject_keywords(node)
            _no_args(lo, node, "Event.set")
            return call(_NONE, "ps_event_set", [], "Event.set")
        if attr == "clear":
            lo._reject_keywords(node)
            _no_args(lo, node, "Event.clear")
            return call(_NONE, "ps_event_clear", [], "Event.clear")
        if attr == "is_set":
            lo._reject_keywords(node)
            _no_args(lo, node, "Event.is_set")
            return call(_BOOL, "ps_event_is_set", [], "Event.is_set")
        if attr == "wait":
            return _wait_call(lo, node, span, receiver, "ps_event_wait", "Event.wait")
        lo.fail(node, "PS2001", f"Event.{attr} is not lowered")
    if cls == "Condition":
        if attr == "acquire":
            lo._reject_keywords(node)
            _no_args(lo, node, "Condition.acquire")
            return call(_BOOL, "ps_cond_acquire", [], "Condition.acquire")
        if attr == "release":
            lo._reject_keywords(node)
            _no_args(lo, node, "Condition.release")
            return call(_NONE, "ps_cond_release", [], "Condition.release")
        if attr == "wait":
            return _wait_call(lo, node, span, receiver, "ps_cond_wait", "Condition.wait")
        if attr == "notify":
            lo._reject_keywords(node)
            _no_args(lo, node, "Condition.notify")
            return call(_NONE, "ps_cond_notify", [], "Condition.notify")
        if attr == "notify_all":
            lo._reject_keywords(node)
            _no_args(lo, node, "Condition.notify_all")
            return call(_NONE, "ps_cond_notify_all", [], "Condition.notify_all")
        lo.fail(node, "PS2001", f"Condition.{attr} is not lowered")
    if cls == "Semaphore":
        if attr == "acquire":
            blocking = True
            for keyword in node.keywords:
                if keyword.arg == "blocking":
                    blocking = _keyword_bool(lo, keyword.value, "blocking")
                else:
                    lo.fail(node, "PS2003", f"Semaphore.acquire has no keyword argument {keyword.arg}")
            if node.args:
                if len(node.args) > 2:
                    lo.fail(node, "PS2003", "Semaphore.acquire expects at most 2 argument(s)")
                blocking = _keyword_bool(lo, node.args[0], "blocking")
            wait = _timeout_seconds(lo, node, 1, span)
            flag = _const_int(1 if blocking else 0, span)
            return call(_BOOL, "ps_sem_acquire", [flag, wait], "Semaphore.acquire")
        if attr == "release":
            lo._reject_keywords(node)
            _no_args(lo, node, "Semaphore.release")
            return call(_NONE, "ps_sem_release", [], "Semaphore.release")
        if attr == "_value":
            lo._reject_keywords(node)
            _no_args(lo, node, "Semaphore._value")
            return call(_INT, "ps_sem_value", [], "Semaphore._value")
        lo.fail(node, "PS2001", f"Semaphore.{attr} is not lowered")
    lo.fail(node, "PS2001", f"{cls} has no method {attr}")
    raise AssertionError("unreachable")
