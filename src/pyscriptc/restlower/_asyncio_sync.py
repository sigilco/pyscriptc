"""The asyncio primitives: Lock, Event, Semaphore, Queue and to_thread.

The records live in `runtime/ps_async.c`, beside the loop: the wait is
cooperative, so the primitive parks the running *task* and the loop
re-queues it when the primitive releases the waiter. Every value is an
opaque pointer to a runtime record, the same shape the threading records
use.

The `await` on a primitive is a suspension, so the call lowers to a marker
(`__ps_await_prim__`) that the IR pass expands into the park-and-suspend
the coroutine frame needs (coro.py). The marker carries the primitive and
the operation; the value the await produces (the queue's element, the
thread's return value) rides in a result slot the frame reads after the
resume.

`to_thread` runs the callable on a real pthread and delivers the result
back to the loop. The loop does not poll the thread: the awaiting task
parks and the loop re-queues it when the worker marks the record done.
The honest boundary is that the loop spins while the thread runs; CPython's
`to_thread` lets the loop run other tasks instead. The stage D3 breakdown
in `.agents/docs/design/async-lowering-plan.md` records the divergence.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.coro import PRIM_MARKER, THREAD_MARKER, _await_result_name, enabled
from pyscriptc.restlower._shared import _const_int

# The primitive records. Each one is a runtime pointer.
_ASYNC_LOCK = {"kind": "class", "name": "AsyncLock"}
_ASYNC_EVENT = {"kind": "class", "name": "AsyncEvent"}
_ASYNC_SEM = {"kind": "class", "name": "AsyncSemaphore"}
_ASYNC_QUEUE = {"kind": "class", "name": "AsyncQueue"}

_INT = {"kind": "int"}
_BOOL = {"kind": "bool"}
_NONE = {"kind": "none"}

# The primitive constructors and the class each one lowers to.
_PRIMS: dict[str, tuple[dict[str, Any], str]] = {
    "Lock": (_ASYNC_LOCK, "ps_alock_new"),
    "Event": (_ASYNC_EVENT, "ps_aevent_new"),
    "Semaphore": (_ASYNC_SEM, "ps_asem_new"),
    "Queue": (_ASYNC_QUEUE, "ps_aqueue_new"),
}


def _const_number(lo: Any, value: ast.expr, what: str) -> int:
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


def _prim_call(lo: Any, node: ast.Call, span: dict[str, Any], expect: Any = None, attr: str = "") -> dict[str, Any]:
    """`asyncio.Lock()`, `Event()`, `Semaphore(value=1)`, `Queue(maxsize=0)`.

    The bound folds at lowering, the way `threading.Semaphore(value)` and
    `queue.Queue(maxsize)` do. The queue's element type comes from the
    annotation, the way a queue annotation carries it.
    """
    spec = _PRIMS.get(attr)
    if spec is None:
        lo.fail(node, "PS2001", f"asyncio.{attr} is not in the lowered subset of asyncio")
        raise AssertionError("unreachable")
    typ, symbol = spec
    if attr in {"Lock", "Event"}:
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", f"asyncio.{attr} expects 0 argument(s)")
        return lo._abi_call("asyncio", attr, [], typ, symbol, span)
    if attr == "Semaphore":
        value = 1
        for keyword in node.keywords:
            if keyword.arg != "value":
                lo.fail(node, "PS2003", f"asyncio.Semaphore has no keyword argument {keyword.arg}")
            value = _const_number(lo, keyword.value, "asyncio.Semaphore value")
        if node.args:
            if len(node.args) > 1:
                lo.fail(node, "PS2003", "asyncio.Semaphore expects at most 1 argument(s)")
            value = _const_number(lo, node.args[0], "asyncio.Semaphore value")
        if value < 0:
            lo.fail(node, "PS1003", "asyncio.Semaphore value is a non-negative int")
        return lo._abi_call("asyncio", attr, [_const_int(value, span)], typ, symbol, span)
    # `asyncio.Queue(maxsize=0)`: unbounded by default. The element type
    # comes from the annotation, so a call without one fences.
    maxsize = 0
    for keyword in node.keywords:
        if keyword.arg != "maxsize":
            lo.fail(node, "PS2003", f"asyncio.Queue has no keyword argument {keyword.arg}")
        maxsize = _const_number(lo, keyword.value, "asyncio.Queue maxsize")
    if node.args:
        if len(node.args) > 1:
            lo.fail(node, "PS2003", "asyncio.Queue expects at most 1 argument(s)")
        maxsize = _const_number(lo, node.args[0], "asyncio.Queue maxsize")
    if maxsize < 0:
        lo.fail(node, "PS1003", "asyncio.Queue maxsize is a non-negative int")
    if expect is None or expect.get("name") != "AsyncQueue" or "elem" not in expect:
        lo.fail(node, "PS1001", "asyncio.Queue() needs a queue annotation")
    from pyscriptc.stdmore import slot_size

    return lo._abi_call(
        "asyncio",
        "Queue",
        [_const_int(maxsize, span), _const_int(slot_size(expect["elem"]), span)],
        expect,
        "ps_aqueue_new",
        span,
    )


def _prim_marker(lo: Any, span: dict[str, Any], prim: dict[str, Any], op: str) -> dict[str, Any]:
    """The marker call the IR pass expands into the park-and-suspend.

    The marker is a builtin call, so the pass recognizes it by name. The
    primitive rides in a local: the frame copies its locals out at the
    suspension, so the record stays alive across the resume.
    """
    slot = lo._temp("prm")
    lo.locals[slot] = prim["type"]
    lo.prelude.append({"kind": "assign", "target": slot, "value": prim, "span": span})
    return {
        "kind": "call",
        "callee": {"kind": "builtin", "name": PRIM_MARKER},
        "args": [{"kind": "name", "id": slot, "type": prim["type"], "span": span}],
        "type": dict(_NONE),
        "span": span,
        "prim_op": op,
    }


def _await_prim(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any], op: str) -> dict[str, Any]:
    """`await lock.acquire()`, `await event.wait()`, `await sem.acquire()`.

    The call is an awaitable: the await parks the task when the primitive
    cannot serve it right away. The lowering puts the marker in the
    prelude, so the await expression itself is the value the frame reads
    after the resume (None for every primitive but the queue).
    """
    if not enabled():
        lo.fail(node, "PS2001", "coroutines are not lowered")
    if lo._coro_current() is None:
        lo.fail(node, "PS2002", f"awaiting {receiver['type'].get('name')} is only lowered inside a coroutine")
    marker = _prim_marker(lo, span, receiver, op)
    lo.prelude.append({"kind": "expr", "expr": marker, "span": span})
    result = _await_result_name(marker["args"][0]["id"])
    lo.locals[result] = dict(_NONE)
    return {"kind": "name", "id": result, "type": dict(_NONE), "span": span}


def _await_queue_get(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`await queue.get()`: the element comes out of the queue.

    The element type rides on the queue's annotation, the way a queue
    annotation carries it. The value lands in a frame local the await
    reads after the resume.
    """
    typ = receiver["type"]
    if "elem" not in typ:
        lo.fail(node, "PS2002", "this queue has no element type")
    elem = typ["elem"]
    if not enabled():
        lo.fail(node, "PS2001", "coroutines are not lowered")
    if lo._coro_current() is None:
        lo.fail(node, "PS2002", "awaiting a queue is only lowered inside a coroutine")
    slot = lo._temp("prq")
    lo.locals[slot] = typ
    lo.prelude.append({"kind": "assign", "target": slot, "value": receiver, "span": span})
    marker = {
        "kind": "call",
        "callee": {"kind": "builtin", "name": PRIM_MARKER},
        "args": [{"kind": "name", "id": slot, "type": typ, "span": span}],
        "type": dict(_NONE),
        "span": span,
        "prim_op": "queue_get",
        "prim_elem": elem,
    }
    lo.prelude.append({"kind": "expr", "expr": marker, "span": span})
    result = _await_result_name(slot)
    lo.locals[result] = elem
    return {"kind": "name", "id": result, "type": elem, "span": span}


def _prim_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """Method dispatch for the asyncio primitive receivers.

    The awaiting methods (`acquire`, `wait`, `get`) are the awaitable
    forms; the plain ones (`release`, `set`, `clear`, `put`) return
    immediately.
    """
    assert isinstance(node.func, ast.Attribute)
    attr = node.func.attr
    cls = receiver["type"]["name"]

    def call(typ: dict[str, Any], symbol: str, args: list[dict[str, Any]], what: str) -> dict[str, Any]:
        return lo._abi_call("asyncio", what, [receiver, *args], typ, symbol, span)

    if cls == "AsyncLock":
        if attr == "acquire":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Lock.acquire expects 0 argument(s)")
            return _await_prim(lo, node, span, receiver, "lock_acquire")
        if attr == "release":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Lock.release expects 0 argument(s)")
            return call(_NONE, "ps_alock_release", [], "Lock.release")
        if attr == "locked":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Lock.locked expects 0 argument(s)")
            return call(_BOOL, "ps_alock_locked", [], "Lock.locked")
    if cls == "AsyncEvent":
        if attr == "wait":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Event.wait expects 0 argument(s)")
            return _await_prim(lo, node, span, receiver, "event_wait")
        if attr == "set":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Event.set expects 0 argument(s)")
            return call(_NONE, "ps_aevent_set", [], "Event.set")
        if attr == "clear":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Event.clear expects 0 argument(s)")
            return call(_NONE, "ps_aevent_clear", [], "Event.clear")
        if attr == "is_set":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Event.is_set expects 0 argument(s)")
            return call(_BOOL, "ps_aevent_is_set", [], "Event.is_set")
    if cls == "AsyncSemaphore":
        if attr == "acquire":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Semaphore.acquire expects 0 argument(s)")
            return _await_prim(lo, node, span, receiver, "sem_acquire")
        if attr == "release":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Semaphore.release expects 0 argument(s)")
            return call(_NONE, "ps_asem_release", [], "Semaphore.release")
        if attr == "_value":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Semaphore._value expects 0 argument(s)")
            return call(_INT, "ps_asem_value", [], "Semaphore._value")
    if cls == "AsyncQueue":
        if attr == "get":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Queue.get expects 0 argument(s)")
            return _await_queue_get(lo, node, span, receiver)
        if attr == "put":
            lo._reject_keywords(node)
            if len(node.args) != 1:
                lo.fail(node, "PS2003", "Queue.put expects 1 argument(s)")
            item = lo._expr(node.args[0])
            return _queue_put(lo, node, span, receiver, item)
        if attr == "qsize":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Queue.qsize expects 0 argument(s)")
            return call(_INT, "ps_aqueue_qsize", [], "Queue.qsize")
        if attr == "empty":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Queue.empty expects 0 argument(s)")
            return call(_BOOL, "ps_aqueue_empty", [], "Queue.empty")
        if attr == "full":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Queue.full expects 0 argument(s)")
            return call(_BOOL, "ps_aqueue_full", [], "Queue.full")
    lo.fail(node, "PS2001", f"{cls}.{attr} is not lowered")
    raise AssertionError("unreachable")


def _queue_put(
    lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any], item: dict[str, Any]
) -> dict[str, Any]:
    """`await queue.put(item)`: the item travels as a pointer to a stashed
    slot, the shape `deque.append` uses. A full queue parks the task."""
    if not enabled():
        lo.fail(node, "PS2001", "coroutines are not lowered")
    if lo._coro_current() is None:
        lo.fail(node, "PS2002", "a queue put is only lowered inside a coroutine")
    return {
        "kind": "call",
        "callee": {"kind": "module", "module": "asyncio", "name": "queue_put"},
        "args": [receiver, item],
        "type": dict(_NONE),
        "span": span,
        "symbol": "ps_aqueue_put",
        "prim_op": "queue_put",
    }


def _to_thread(lo: Any, node: ast.Call, span: dict[str, Any], expect: Any = None, attr: str = "") -> dict[str, Any]:
    """`asyncio.to_thread(fn, *args)`: run the callable on a worker thread.

    The callable is a `func` value, so it is a callable pair. The bound
    arguments ride in the pair's env, the way `threading.Thread(target=f,
    args=(...))` and `functools.partial` bind them: one synthesized
    env-first stub calls the target pair with the bound values prepended.

    The call is an awaitable: the await parks the task until the worker
    marks the record done, and the frame reads the result out of the
    record after the resume.
    """
    if not enabled():
        lo.fail(node, "PS2001", "coroutines are not lowered")
    if lo._coro_current() is None:
        lo.fail(node, "PS2002", "asyncio.to_thread is only lowered inside a coroutine")
    lo._reject_keywords(node)
    if not node.args:
        lo.fail(node, "PS2003", "asyncio.to_thread expects at least 1 argument")
    target = lo._expr(node.args[0])
    ftype = target["type"]
    if ftype.get("kind") != "func":
        lo.fail(node, "PS1003", "asyncio.to_thread takes a callable")
    bound_nodes = list(node.args[1:])
    if len(bound_nodes) > len(ftype["params"]):
        lo.fail(node, "PS2003", f"asyncio.to_thread target expects {len(ftype['params'])} argument(s)")
    env: list[dict[str, Any]] = []
    bound: list[dict[str, Any]] = []
    for index, arg_node in enumerate(bound_nodes):
        expect = ftype["params"][index]
        value = lo._expr(arg_node, expect)
        lo._expect(arg_node, value["type"], expect, f"asyncio.to_thread argument {index + 1}")
        env.append({"init": value, "type": expect})
        bound.append(expect)
    env.append({"init": target, "type": ftype})
    ret = ftype["return"]
    # The record the worker writes its result into. The size is the result
    # slot's size, so the runtime copies exactly the value's bytes.
    from pyscriptc.emit._ir import _elem_size

    slot = lo._temp("thc")
    lo.locals[slot] = {"kind": "class", "name": "ThreadCall"}
    # The record rides in the pair's env, after the inner pair, so the
    # worker stub can store the result into it. The `init` form packs the
    # record into a heap cell: a stack slot would die when the coroutine
    # wrapper suspends, and the worker reads the env after that.
    env.append(
        {
            "init": {"kind": "name", "id": slot, "type": {"kind": "class", "name": "ThreadCall"}, "span": span},
            "type": {"kind": "class", "name": "ThreadCall"},
        }
    )
    # The record comes first: the pair's env names its slot, so the slot
    # has to hold the record before the pair is built.
    made = {
        "kind": "call",
        "callee": {"kind": "module", "module": "asyncio", "name": "to_thread_new"},
        "args": [],
        "type": {"kind": "class", "name": "ThreadCall"},
        "span": span,
        "symbol": "ps_to_thread_new",
        "thread": {"result": ret, "size": _elem_size(ret)},
    }
    lo.prelude.append({"kind": "assign", "target": slot, "value": made, "span": span})
    pair = {
        "kind": "closure",
        "name": "ps.thread",
        "env": env,
        "type": {"kind": "func", "params": ftype["params"][len(bound) :], "return": ret},
        "thread_stub": {
            "bound": bound,
            "params": ftype["params"][len(bound) :],
            "return": ret,
        },
        "span": span,
    }
    start = {
        "kind": "call",
        "callee": {"kind": "module", "module": "asyncio", "name": "to_thread_start"},
        "args": [{"kind": "name", "id": slot, "type": made["type"], "span": span}, pair],
        "type": dict(_NONE),
        "span": span,
        "symbol": "ps_to_thread_start",
        "thread": {"result": ret, "size": _elem_size(ret)},
    }
    lo.prelude.append({"kind": "expr", "expr": start, "span": span})
    marker = {
        "kind": "call",
        "callee": {"kind": "builtin", "name": THREAD_MARKER},
        "args": [{"kind": "name", "id": slot, "type": made["type"], "span": span}],
        "type": dict(_NONE),
        "span": span,
        "thread_elem": ret,
    }
    lo.prelude.append({"kind": "expr", "expr": marker, "span": span})
    result = _await_result_name(slot)
    lo.locals[result] = ret
    return {"kind": "name", "id": result, "type": ret, "span": span}
