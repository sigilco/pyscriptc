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

from pyscriptc.coro import SLEEP_MARKER
from pyscriptc.ir import types_equal
from pyscriptc.restlower._shared import _as_float


def _frame_of(lo: Any, node: ast.expr, what: str) -> tuple[dict[str, Any], dict[str, Any]]:
    value = lo._expr(node)
    name = str(value["type"].get("name", ""))
    if value["type"].get("kind") != "class" or not name.startswith("coro."):
        lo.fail(node, "PS2002", f"{what} takes a coroutine, not another value")
    elem = None
    for plan in lo._coro_plans().values():
        if plan.frame == name:
            elem = plan.elem
    if elem is None:
        lo.fail(node, "PS2002", f"{what} takes a compiled coroutine call")
    return value, elem


def _run_stmt(lo: Any, span: dict[str, Any], symbol: str, args: list[dict[str, Any]]) -> None:
    call = lo._abi_call("asyncio", "run", args, {"kind": "none"}, symbol, span)
    lo.prelude.append({"kind": "expr", "expr": call, "span": span})


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
    _run_stmt(lo, span, "ps_loop_reset", [])
    for frame in frames:
        _run_stmt(lo, span, "ps_loop_ready", [frame])
    _run_stmt(lo, span, "ps_loop_run", [])
    assert elem is not None
    return {
        "kind": "list",
        "elems": [
            {"kind": "attr", "value": frame, "name": ".ps_result", "type": elem, "span": span}
            for frame in frames
        ],
        "type": {"kind": "list", "elem": elem},
        "span": span,
    }
