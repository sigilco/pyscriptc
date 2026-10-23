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

from pyscriptc.coro import SLEEP_MARKER, TASK_CLASS, enabled
from pyscriptc.ir import types_equal
from pyscriptc.restlower._shared import _as_float


def _frame_of(lo: Any, node: ast.expr, what: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not enabled():
        lo.fail(node, "PS2001", "coroutines are not lowered")
    value = lo._expr(node)
    name = str(value["type"].get("name", ""))
    if value["type"].get("kind") != "class" or not name.startswith("coro."):
        lo.fail(node, "PS2002", f"{what} takes a coroutine, not another value")
    elem = None
    plan = lo._coro_plan_for_frame(name)
    if plan is not None:
        elem = plan.elem
    if elem is None:
        lo.fail(node, "PS2002", f"{what} takes a compiled coroutine call")
    return value, elem


def _run_stmt(lo: Any, span: dict[str, Any], symbol: str, args: list[dict[str, Any]]) -> None:
    call = lo._abi_call("asyncio", "run", args, {"kind": "none"}, symbol, span)
    lo.prelude.append({"kind": "expr", "expr": call, "span": span})


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
    lo.locals[slot] = frame["type"]
    lo.prelude.append({"kind": "assign", "target": slot, "value": frame, "span": span})
    pinned = {"kind": "name", "id": slot, "type": frame["type"], "span": span}
    task_t = {"kind": "class", "name": TASK_CLASS, "elem": elem}
    return lo._abi_call("asyncio", "create_task", [pinned], task_t, "ps_task_new", span)


def _asyncio_run(lo: Any, node: ast.Call, span: dict[str, Any], expect: Any = None, attr: str = "") -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "asyncio.run expects 1 argument(s)")
    frame, elem = _frame_of(lo, node.args[0], "asyncio.run")
    # One frame object drives the whole run: pin it so the loop call and the
    # result read cannot allocate two different coroutines.
    slot = lo._temp("run")
    lo.locals[slot] = frame["type"]
    lo.prelude.append({"kind": "assign", "target": slot, "value": frame, "span": span})
    pinned = {"kind": "name", "id": slot, "type": frame["type"], "span": span}
    _run_stmt(lo, span, "ps_loop_run_root", [pinned])
    return {
        "kind": "attr",
        "value": pinned,
        "name": ".ps_result",
        "type": elem,
        "span": span,
    }


def _asyncio_sleep(lo: Any, node: ast.Call, span: dict[str, Any], expect: Any = None, attr: str = "") -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "asyncio.sleep expects 1 argument(s)")
    if not enabled():
        lo.fail(node, "PS2001", "coroutines are not lowered")
    delay = _as_float(lo, node, lo._expr(node.args[0]), span, "asyncio.sleep")
    if lo._coro_current() is None:
        lo.fail(node, "PS2002", "asyncio.sleep is only lowered inside a coroutine")
    return {
        "kind": "call",
        "callee": {"kind": "builtin", "name": SLEEP_MARKER},
        "args": [delay],
        "type": {"kind": "none"},
        "span": span,
    }


def _task_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """Method dispatch for a task handle: `done()` and `result()`."""
    assert isinstance(node.func, ast.Attribute)
    attr = node.func.attr
    elem = receiver["type"]["elem"]

    if attr == "done":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "Task.done expects 0 argument(s)")
        return lo._abi_call("asyncio", "task.done", [receiver], {"kind": "bool"}, "ps_task_done", span)
    if attr == "result":
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "Task.result expects 0 argument(s)")
        # Same symbol as the await's read: the emitter hands back the value
        # out of the task's frame, at the element size `elem` carries.
        return lo._abi_call("asyncio", "task.result", [receiver], elem, "ps_task_result", span)
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
        lo.locals[slot] = frame["type"]
        lo.prelude.append({"kind": "assign", "target": slot, "value": frame, "span": span})
        frames.append({"kind": "name", "id": slot, "type": frame["type"], "span": span})
    # No reset here: gather may run inside an outer loop (a gather within a
    # coroutine), and resetting would drop the outer tasks' wait rows. The
    # shared loop drives the new frames alongside whatever is already there.
    for frame in frames:
        _run_stmt(lo, span, "ps_loop_ready", [frame])
    _run_stmt(lo, span, "ps_loop_run", [])
    assert elem is not None
    return {
        "kind": "list",
        "elems": [
            {"kind": "attr", "value": frame, "name": ".ps_result", "type": elem, "span": span} for frame in frames
        ],
        "type": {"kind": "list", "elem": elem},
        "span": span,
    }
