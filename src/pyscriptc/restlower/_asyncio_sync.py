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
import copy
import hashlib
import json
from typing import Any

from pyscriptc import _irnode as _ir
from pyscriptc.coro import (
    PRIM_MARKER,
    RETURN_MARKER,
    THREAD_MARKER,
    FramePlan,
    _await_result_name,
    _call_node,
    _return_node,
    enabled,
)
from pyscriptc.restlower._shared import (
    _asg,
    _call_builtin,
    _call_module,
    _class,
    _const_int,
    _expr_stmt,
    _field_name,
    _field_type,
    _mk_span,
    _nm,
    _node_kind,
    _node_type,
    _node_type_kind,
    _node_value,
    _t_leaf,
    _type_kind,
    _type_name,
)
from pyscriptc.tirserde import dict_to_node, dict_to_type, node_to_dict

# The primitive constructors and the class each one lowers to, spelled at
# the call site: a module-level record would be a call, and a module
# assignment must be a constant.


def _const_number(lo: Any, value: ast.expr, what: str) -> int:
    """A constant int, including the unary-minus spelling."""
    arg = lo._expr(value)
    if _node_kind(arg) == "const" and _node_type_kind(arg) in {"int", "bool"}:
        return int(_node_value(arg))
    if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.USub):
        inner = lo._expr(value.operand)
        if _node_kind(inner) == "const" and _node_type_kind(inner) == "int":
            return -int(_node_value(inner))
    lo.fail(value, "PS1003", f"{what} is a constant int")
    raise AssertionError("unreachable")


def _prim_call(lo: Any, node: ast.Call, span: dict[str, Any], expect: Any = None, attr: str = "") -> dict[str, Any]:
    """`asyncio.Lock()`, `Event()`, `Semaphore(value=1)`, `Queue(maxsize=0)`.

    The bound folds at lowering, the way `threading.Semaphore(value)` and
    `queue.Queue(maxsize)` do. The queue's element type comes from the
    annotation, the way a queue annotation carries it.
    """
    prims: dict[str, tuple[dict[str, Any], str]] = {
        "Lock": (_class("AsyncLock"), "ps_alock_new"),
        "Event": (_class("AsyncEvent"), "ps_aevent_new"),
        "Semaphore": (_class("AsyncSemaphore"), "ps_asem_new"),
        "Queue": (_class("AsyncQueue"), "ps_aqueue_new"),
    }
    spec = prims.get(attr)
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
    if expect is None or _type_name(expect) != "AsyncQueue" or "elem" not in expect:
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


def _prim_marker(lo: Any, span: dict[str, Any], prim: dict[str, Any], op: str) -> tuple[dict[str, Any], str]:
    """The marker call the IR pass expands into the park-and-suspend.

    The marker is a builtin call, so the pass recognizes it by name. The
    primitive rides in a local: the frame copies its locals out at the
    suspension, so the record stays alive across the resume. Returns the
    marker and the slot the await's result name derives from.
    """
    slot = lo._temp("prm")
    prim_t = _node_type(prim)
    lo.locals[slot] = prim_t
    lo.prelude.append(_asg(slot, prim, span))
    marker = _call_builtin(PRIM_MARKER, [_nm(slot, prim_t, span)], _t_leaf("none"), span)
    marker["prim_op"] = op
    return marker, slot


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
        lo.fail(node, "PS2002", f"awaiting {_type_name(_node_type(receiver))} is only lowered inside a coroutine")
    marker, slot = _prim_marker(lo, span, receiver, op)
    lo.prelude.append(_expr_stmt(marker, span))
    result = _await_result_name(slot)
    lo.locals[result] = _t_leaf("none")
    return _nm(result, _t_leaf("none"), span)


def _await_queue_get(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """`await queue.get()`: the element comes out of the queue.

    The element type rides on the queue's annotation, the way a queue
    annotation carries it. The value lands in a frame local the await
    reads after the resume.
    """
    typ = _node_type(receiver)
    if "elem" not in typ:
        lo.fail(node, "PS2002", "this queue has no element type")
    elem = typ["elem"]
    if not enabled():
        lo.fail(node, "PS2001", "coroutines are not lowered")
    if lo._coro_current() is None:
        lo.fail(node, "PS2002", "awaiting a queue is only lowered inside a coroutine")
    slot = lo._temp("prq")
    lo.locals[slot] = typ
    lo.prelude.append(_asg(slot, receiver, span))
    marker = _call_builtin(PRIM_MARKER, [_nm(slot, typ, span)], _t_leaf("none"), span)
    marker["prim_op"] = "queue_get"
    marker["prim_elem"] = elem
    lo.prelude.append(_expr_stmt(marker, span))
    result = _await_result_name(slot)
    lo.locals[result] = elem
    return _nm(result, elem, span)


def _prim_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """Method dispatch for the asyncio primitive receivers.

    The awaiting methods (`acquire`, `wait`, `get`) are the awaitable
    forms; the plain ones (`release`, `set`, `clear`, `put`) return
    immediately.
    """
    assert isinstance(node.func, ast.Attribute)
    attr = node.func.attr
    cls = _type_name(_node_type(receiver))

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
            return call(_t_leaf("none"), "ps_alock_release", [], "Lock.release")
        if attr == "locked":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Lock.locked expects 0 argument(s)")
            return call(_t_leaf("bool"), "ps_alock_locked", [], "Lock.locked")
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
            return call(_t_leaf("none"), "ps_aevent_set", [], "Event.set")
        if attr == "clear":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Event.clear expects 0 argument(s)")
            return call(_t_leaf("none"), "ps_aevent_clear", [], "Event.clear")
        if attr == "is_set":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Event.is_set expects 0 argument(s)")
            return call(_t_leaf("bool"), "ps_aevent_is_set", [], "Event.is_set")
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
            return call(_t_leaf("none"), "ps_asem_release", [], "Semaphore.release")
        if attr == "_value":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Semaphore._value expects 0 argument(s)")
            return call(_t_leaf("int"), "ps_asem_value", [], "Semaphore._value")
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
            return call(_t_leaf("int"), "ps_aqueue_qsize", [], "Queue.qsize")
        if attr == "empty":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Queue.empty expects 0 argument(s)")
            return call(_t_leaf("bool"), "ps_aqueue_empty", [], "Queue.empty")
        if attr == "full":
            lo._reject_keywords(node)
            if node.args:
                lo.fail(node, "PS2003", "Queue.full expects 0 argument(s)")
            return call(_t_leaf("bool"), "ps_aqueue_full", [], "Queue.full")
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
    call = _call_module("asyncio", "queue_put", "ps_aqueue_put", [receiver, item], _t_leaf("none"), span)
    call["prim_op"] = "queue_put"
    return call


def _to_thread(lo: Any, node: ast.Call, span: dict[str, Any], expect: Any = None, attr: str = "") -> dict[str, Any]:
    """`asyncio.to_thread(fn, *args)`: a coroutine frame over a worker call.

    The call lowers to a synthesized coroutine, the same shape `async def`
    lowers to: one `coro.ps_to_thread_N` class per callable signature
    whose `resume` starts the worker on the first poll and parks on the
    record until the worker marks it done. `create_task`, `gather` and
    `wait_for` only accept frames, so this is the shape that lets them
    compose with `to_thread` (issue #400); a plain `await` polls the frame
    through `ps_await_poll` exactly like a user coroutine.
    """
    if not enabled():
        lo.fail(node, "PS2001", "coroutines are not lowered")
    lo._reject_keywords(node)
    if not node.args:
        lo.fail(node, "PS2003", "asyncio.to_thread expects at least 1 argument")
    target = lo._expr(node.args[0])
    ftype = _node_type(target)
    if _type_kind(ftype) != "func":
        lo.fail(node, "PS1003", "asyncio.to_thread takes a callable")
    bound_nodes = list(node.args[1:])
    if len(bound_nodes) > len(ftype["params"]):
        lo.fail(node, "PS2003", f"asyncio.to_thread target expects {len(ftype['params'])} argument(s)")
    bound: list[dict[str, Any]] = []
    values: list[dict[str, Any]] = []
    for index, arg_node in enumerate(bound_nodes):
        expect_arg = ftype["params"][index]
        value = lo._expr(arg_node, expect_arg)
        lo._expect(arg_node, _node_type(value), expect_arg, f"asyncio.to_thread argument {index + 1}")
        bound.append(expect_arg)
        values.append(value)
    fname, frame_t = _to_thread_frame(lo, span, ftype, bound)
    return _call_node(node_to_dict(_ir.function_callee(fname)), [target, *values], frame_t, span)


def _to_thread_frame(
    lo: Any, span: dict[str, Any], ftype: dict[str, Any], bound: list[dict[str, Any]]
) -> tuple[str, dict[str, Any]]:
    """The synthesized coroutine behind `to_thread` for one signature.

    Appends a plain function record to `nested_fns` and registers its
    `FramePlan`, so `lower_resumables` rewrites it into the wrapper, frame
    class and `coro.*.resume` forwarder the way it rewrites an `async
    def`. The body's suspendable call is the same `THREAD_MARKER`
    expansion the D3 caller took: park the running task on the record,
    poll the worker, and read the result back after the resume.
    """
    frames: dict[str, tuple[str, dict[str, Any]]] = lo.__dict__.setdefault("to_thread_frames", {})
    digest = hashlib.sha1(json.dumps([ftype, bound], sort_keys=True).encode()).hexdigest()[:10]
    name = f"ps_to_thread_{digest}"
    frame = f"coro.{name}"
    hit = frames.get(name)
    if hit is not None:
        return hit
    from pyscriptc.emit._ir import _elem_size

    ret = ftype["return"]
    result_size = _elem_size(ret) if ret.get("kind") != "none" else 0
    frame_t = _class(frame)
    threadcall = _class("ThreadCall")
    record_slot = ".ps_th_rec"
    result = _await_result_name(record_slot)
    ir_name = lo._ir_name(name)
    params = [{"name": ".ps_th_f", "type": ftype}]
    params += [{"name": f".ps_th_a{index}", "type": arg_t} for index, arg_t in enumerate(bound)]
    # The worker stub calls the pair with the bound arguments prepended,
    # the shape `threading.Thread(target=f, args=(...))` binds, and writes
    # the outcome into the record the env carries last. The env order is
    # the stub's contract: bound arguments, the inner pair, the record.
    env = [
        _ir.EnvItem(dict_to_node(_nm(_field_name(param), _field_type(param), span)), dict_to_type(_field_type(param)))
        for param in params[1:]
    ]
    env.append(_ir.EnvItem(dict_to_node(_nm(".ps_th_f", ftype, span)), dict_to_type(ftype)))
    env.append(_ir.EnvItem(dict_to_node(_nm(record_slot, threadcall, span)), dict_to_type(threadcall)))
    made = _call_module("asyncio", "to_thread_new", "ps_to_thread_new", [], threadcall, span)
    made["thread"] = {"result": ret, "size": result_size}
    remaining = ftype["params"][len(bound) :]
    pair = node_to_dict(
        _ir.closure(
            "ps.thread",
            env,
            _ir.t_func([dict_to_type(param) for param in remaining], dict_to_type(ret)),
            _mk_span(span),
            thread_stub=_ir.Stub(
                [dict_to_type(param) for param in bound],
                [dict_to_type(param) for param in remaining],
                dict_to_type(ret),
            ),
        )
    )
    start = _call_module(
        "asyncio",
        "to_thread_start",
        "ps_to_thread_start",
        [_nm(record_slot, threadcall, span), pair],
        _t_leaf("none"),
        span,
    )
    start["thread"] = {"result": ret, "size": result_size}
    marker = _call_builtin(THREAD_MARKER, [_nm(record_slot, threadcall, span)], _t_leaf("none"), span)
    marker["thread_elem"] = ret
    ret_mark = _call_node(node_to_dict(_ir.builtin(RETURN_MARKER)), [_nm(result, ret, span)], _t_leaf("none"), span)
    lo.nested_fns.append(
        {
            "name": ir_name,
            "params": params,
            "returnType": _t_leaf("none"),
            "locals": [*params, {"name": record_slot, "type": threadcall}, {"name": result, "type": ret}],
            "body": [
                _asg(record_slot, made, span),
                _expr_stmt(start, span),
                _expr_stmt(marker, span),
                _return_node(ret_mark, span),
            ],
            "span": span,
        }
    )
    lo._coro_plans()[name] = FramePlan("coroutine", name, frame, ret, params, ir_name)
    lo.classes[frame] = {
        "fields": [],
        "own_fields": [],
        "methods": {"resume": ([{"name": "self", "type": frame_t}], _t_leaf("int"))},
        "method_kind": {},
        "method_owner": {},
        "base": None,
        "coro": "coroutine",
        "coro_elem": copy.deepcopy(ret),
    }
    lo.known_classes.add(frame)
    frames[name] = (ir_name, frame_t)
    return ir_name, frame_t
