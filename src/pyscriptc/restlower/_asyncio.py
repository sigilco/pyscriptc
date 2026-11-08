"""asyncio.run, asyncio.sleep and asyncio.gather on compiled coroutine frames.

The event loop is `runtime/ps_loop.c`: run-to-completion over frames whose
field 1 is the resume callable cell (see coro.py). `run` drives one frame and
reads its result; `sleep` arms a timer for the calling frame and suspends it
(the IR pass expands the marker); `gather` queues every frame and drives them
together, then collects the results in argument order.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.coro import SLEEP_MARKER, TASK_CLASS, WAIT_FOR_MARKER, enabled
from pyscriptc.ir import types_equal
from pyscriptc.restlower._shared import (
    _as_float,
    _asg,
    _call_builtin,
    _class_elem,
    _expr_stmt,
    _field,
    _list_lit,
    _nm,
    _node_type,
    _t_leaf,
    _type_kind,
    _type_name,
)


def _frame_of(lo: Any, node: ast.expr, what: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not enabled():
        lo.fail(node, "PS2001", "coroutines are not lowered")
    value = lo._expr(node)
    typ = _node_type(value)
    name = _type_name(typ)
    if _type_kind(typ) != "class" or not name.startswith("coro."):
        lo.fail(node, "PS2002", f"{what} takes a coroutine, not another value")
    elem = None
    plan = lo._coro_plan_for_frame(name)
    if plan is not None:
        elem = plan.elem
    if elem is None:
        lo.fail(node, "PS2002", f"{what} takes a compiled coroutine call")
    return value, elem


def _run_stmt(lo: Any, span: dict[str, Any], symbol: str, args: list[dict[str, Any]]) -> None:
    call = lo._abi_call("asyncio", "run", args, _t_leaf("none"), symbol, span)
    lo.prelude.append(_expr_stmt(call, span))


def _asyncio_create_task(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: Any = None, attr: str = ""
) -> dict[str, Any]:
    """`asyncio.create_task(coro)`: wrap the coroutine frame in a task.

    The task is a runtime record that outlives a step, so the handle stays
    valid across a suspension. The task starts on the next loop iteration,
    never inline: that is CPython's `create_task` semantics. The handle's
    type is the `Task` class, parameterized by the coroutine's result type,
    so `await task` reads the right shape.
    """
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "asyncio.create_task expects 1 argument(s)")
    lo._reject_keywords(node)
    frame, elem = _frame_of(lo, node.args[0], "asyncio.create_task")
    # Pin the frame: the task wraps one frame object, not a fresh call.
    slot = lo._temp("tsk")
    frame_t = _node_type(frame)
    lo.locals[slot] = frame_t
    lo.prelude.append(_asg(slot, frame, span))
    pinned = _nm(slot, frame_t, span)
    task_t = _class_elem(TASK_CLASS, elem)
    return lo._abi_call("asyncio", "create_task", [pinned], task_t, "ps_task_new", span)


def _asyncio_wait_for(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: Any = None, attr: str = ""
) -> dict[str, Any]:
    """`asyncio.wait_for(aw, timeout)`: run the awaitable under a deadline.

    The awaited coroutine runs as a task, so the deadline can fire while it
    is still running: the loop cancels the task and the awaiting frame
    raises TimeoutError. A task handle is accepted too (CPython's
    `wait_for` takes any awaitable), and the deadline then applies to the
    task the caller already has.
    """
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "asyncio.wait_for expects 2 argument(s)")
    lo._reject_keywords(node)
    delay = _as_float(lo, node, lo._expr(node.args[1]), span, "asyncio.wait_for")
    awaited = lo._expr(node.args[0])
    awaited_t = _node_type(awaited)
    if _type_kind(awaited_t) == "class" and _type_name(awaited_t) == TASK_CLASS:
        # A task handle: the deadline applies to the task the caller holds.
        task_t = awaited_t
        task_slot = lo._temp("wtk")
        lo.locals[task_slot] = task_t
        lo.prelude.append(_asg(task_slot, awaited, span))
    else:
        frame, elem = _frame_of(lo, node.args[0], "asyncio.wait_for")
        # The task wraps one frame object: pin it so the task and the marker
        # name the same coroutine.
        frame_slot = lo._temp("wfr")
        frame_t = _node_type(frame)
        lo.locals[frame_slot] = frame_t
        lo.prelude.append(_asg(frame_slot, frame, span))
        pinned = _nm(frame_slot, frame_t, span)
        task_t = _class_elem(TASK_CLASS, elem)
        task_slot = lo._temp("wtk")
        lo.locals[task_slot] = task_t
        task = lo._abi_call("asyncio", "create_task", [pinned], task_t, "ps_task_new", span)
        lo.prelude.append(_asg(task_slot, task, span))
    task_name = _nm(task_slot, task_t, span)
    call = _call_builtin(WAIT_FOR_MARKER, [task_name, delay], task_t["elem"], span)
    call["wait_for"] = True
    return call


def _asyncio_run(lo: Any, node: ast.Call, span: dict[str, Any], expect: Any = None, attr: str = "") -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "asyncio.run expects 1 argument(s)")
    frame, elem = _frame_of(lo, node.args[0], "asyncio.run")
    # One frame object drives the whole run: pin it so the loop call and the
    # result read cannot allocate two different coroutines.
    slot = lo._temp("run")
    frame_t = _node_type(frame)
    lo.locals[slot] = frame_t
    lo.prelude.append(_asg(slot, frame, span))
    pinned = _nm(slot, frame_t, span)
    _run_stmt(lo, span, "ps_loop_run_root", [pinned])
    return _field(pinned, ".ps_result", elem, span)


def _asyncio_sleep(lo: Any, node: ast.Call, span: dict[str, Any], expect: Any = None, attr: str = "") -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "asyncio.sleep expects 1 argument(s)")
    if not enabled():
        lo.fail(node, "PS2001", "coroutines are not lowered")
    delay = _as_float(lo, node, lo._expr(node.args[0]), span, "asyncio.sleep")
    if lo._coro_current() is None:
        lo.fail(node, "PS2002", "asyncio.sleep is only lowered inside a coroutine")
    return _call_builtin(SLEEP_MARKER, [delay], _t_leaf("none"), span)


def _task_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """Method dispatch for a task handle: `done()`, `result()`, `cancel()` and
    `cancelled()`."""
    assert isinstance(node.func, ast.Attribute)
    attr = node.func.attr
    elem = _node_type(receiver)["elem"]

    if attr == "done":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "Task.done expects 0 argument(s)")
        return lo._abi_call("asyncio", "task.done", [receiver], _t_leaf("bool"), "ps_task_done", span)
    if attr == "result":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "Task.result expects 0 argument(s)")
        # Same symbol as the await's read: the emitter hands back the value
        # out of the task's frame, at the element size `elem` carries.
        return lo._abi_call("asyncio", "task.result", [receiver], elem, "ps_task_result", span)
    if attr == "cancel":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "Task.cancel expects 0 argument(s)")
        # CPython's cancel returns True when the cancel landed (the task was
        # not already done), False otherwise.
        return lo._abi_call("asyncio", "task.cancel", [receiver], _t_leaf("bool"), "ps_task_cancel", span)
    if attr == "cancelled":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "Task.cancelled expects 0 argument(s)")
        return lo._abi_call("asyncio", "task.cancelled", [receiver], _t_leaf("bool"), "ps_task_cancelled", span)
    lo.fail(node, "PS2001", f"Task.{attr} is not lowered")
    raise AssertionError("unreachable")


def _asyncio_gather(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: Any = None, attr: str = ""
) -> dict[str, Any]:
    if not node.args:
        lo.fail(node, "PS2003", "asyncio.gather expects at least 1 argument")
    frames: list[dict[str, Any]] = []
    elem: dict[str, Any] | None = None
    for arg in node.args:
        frame, frame_elem = _frame_of(lo, arg, "asyncio.gather")
        if elem is None:
            elem = frame_elem
        elif not types_equal(elem, frame_elem):
            lo.fail(arg, "PS2002", "asyncio.gather takes coroutines of one result type")
        # Pin each frame so the ready calls and the result reads name one
        # coroutine per argument, not fresh allocations of the same call.
        slot = lo._temp("gth")
        frame_t = _node_type(frame)
        lo.locals[slot] = frame_t
        lo.prelude.append(_asg(slot, frame, span))
        frames.append(_nm(slot, frame_t, span))
    # No reset here: gather may run inside an outer loop (a gather within a
    # coroutine), and resetting would drop the outer tasks' wait rows. The
    # shared loop drives the new frames alongside whatever is already there.
    for frame in frames:
        _run_stmt(lo, span, "ps_loop_ready", [frame])
    _run_stmt(lo, span, "ps_loop_run", [])
    assert elem is not None
    return _list_lit([_field(frame, ".ps_result", elem, span) for frame in frames], elem, span)
