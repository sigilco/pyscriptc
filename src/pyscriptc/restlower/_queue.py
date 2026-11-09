"""queue.Queue, LifoQueue and SimpleQueue on the runtime queues.

The design doc (threads-plan.md, Stage C) scopes the surface. Every value is
an opaque pointer to a runtime record, the same shape `collections.deque`
uses: the mutex and the two condvars stay runtime details. The element type
comes from the annotation (`q: queue.Queue[int]`), the way deque takes its
element type.

The element rides to the runtime as a pointer to a stashed slot, the shape
`deque.append` uses: the queue stores element-sized slots, and the compiler
knows the size.

`queue.Empty` and `queue.Full` ride the reserved code space next to the
coroutine exceptions: `get_nowait()` raises the code, and `except Empty:`
matches it through the reserved-code parent walk in ps_rt.c.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import (
    _binary,
    _call_builtin,
    _cast_float,
    _const_int,
    _const_text,
    _node_kind,
    _node_type,
    _node_type_kind,
    _node_value,
    _t_leaf,
    _type_kind,
    _type_name,
)

# kind rides to ps_queue_new: 0 Queue, 1 LifoQueue, 2 SimpleQueue.
_KINDS = {"Queue": 0, "LifoQueue": 1, "SimpleQueue": 2}


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


def _keyword_bool(lo: Any, value: ast.expr, what: str) -> bool:
    """`block` is a constant bool in this subset: it selects between the
    blocking and the non-blocking call in the runtime."""
    arg = lo._expr(value)
    kind = _node_type_kind(arg)
    if _node_kind(arg) == "const" and kind == "bool":
        return bool(_node_value(arg))
    if _node_kind(arg) == "const" and kind == "int":
        return int(_node_value(arg)) != 0
    lo.fail(value, "PS1003", f"{what} is a constant bool")
    raise AssertionError("unreachable")


def _timeout_nanos(lo: Any, value: ast.expr | None, span: dict[str, Any]) -> dict[str, Any]:
    """The timeout in seconds as an int expression of nanoseconds.

    None blocks forever, the -1 marker. The runtime measures the timeout
    against the monotonic clock, so the value is nanoseconds.
    """
    if value is None:
        return _const_int(-1, span)
    arg = lo._expr(value)
    if _node_type_kind(arg) == "none":
        return _const_int(-1, span)
    if _node_type_kind(arg) == "int":
        arg = _cast_float(arg, span)
    if _node_type_kind(arg) != "float":
        lo.fail(value, "PS1003", "the timeout is a number or None")
    billion = _const_text("1000000000.0", "float", span)
    return _call_builtin("int", [_binary("*", arg, billion, _t_leaf("float"), span)], _t_leaf("int"), span)


def _queue_call(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None, attr: str
) -> dict[str, Any]:
    """`queue.Queue(maxsize)`, `LifoQueue(maxsize)`, `SimpleQueue()`.

    The bound folds at lowering, like `Semaphore(value)`: maxsize is a
    constant int, default 0 (unbounded). The element type comes from the
    expected type, the way a deque takes its element type; a call without
    an annotation fences, because the record stores element-sized slots.
    """
    kind = _KINDS.get(attr)
    if kind is None:
        lo.fail(node, "PS2001", f"queue.{attr} is not in the lowered subset of queue")
        raise AssertionError("unreachable")
    maxsize = 0
    for keyword in node.keywords:
        if keyword.arg != "maxsize":
            lo.fail(node, "PS2003", f"queue.{attr} has no keyword argument {keyword.arg}")
        maxsize = _const_number(lo, keyword.value, f"queue.{attr} maxsize")
    if node.args:
        if len(node.args) > 1:
            lo.fail(node, "PS2003", f"queue.{attr} expects at most 1 argument(s)")
        maxsize = _const_number(lo, node.args[0], f"queue.{attr} maxsize")
    if maxsize < 0:
        lo.fail(node, "PS1003", f"queue.{attr} maxsize is a non-negative int")
    if expect is None or _type_name(expect) != "Queue" or "elem" not in expect:
        lo.fail(node, "PS1001", f"queue.{attr}() needs a queue annotation")
    elem = expect["elem"]
    if _type_kind(elem) == "bool":
        lo.fail(node, "PS2002", "list[bool] is not lowered")
    from pyscriptc.stdmore import slot_size

    return lo._abi_call(
        "queue",
        attr,
        [_const_int(maxsize, span), _const_int(kind, span), _const_int(slot_size(elem), span)],
        expect,
        "ps_queue_new",
        span,
    )


def _no_args(lo: Any, node: ast.Call, what: str) -> None:
    if node.args:
        lo.fail(node, "PS2003", f"{what} expects 0 argument(s)")


def _queue_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """Method dispatch for Queue, LifoQueue and SimpleQueue receivers."""
    assert isinstance(node.func, ast.Attribute)
    attr = node.func.attr
    cls = _type_name(_node_type(receiver))
    simple = cls == "SimpleQueue"

    def call(typ: dict[str, Any], symbol: str, args: list[dict[str, Any]], what: str) -> dict[str, Any]:
        return lo._abi_call("queue", what, [receiver, *args], typ, symbol, span)

    if attr == "qsize":
        lo._reject_keywords(node)
        _no_args(lo, node, f"{cls}.qsize")
        return call(_t_leaf("int"), "ps_queue_qsize", [], f"{cls}.qsize")
    if attr == "empty":
        lo._reject_keywords(node)
        _no_args(lo, node, f"{cls}.empty")
        return call(_t_leaf("bool"), "ps_queue_empty", [], f"{cls}.empty")
    if attr == "full":
        lo._reject_keywords(node)
        _no_args(lo, node, f"{cls}.full")
        return call(_t_leaf("bool"), "ps_queue_full", [], f"{cls}.full")
    if attr in {"put", "put_nowait"}:
        return _put_call(lo, node, span, receiver, attr == "put_nowait")
    if attr in {"get", "get_nowait"}:
        return _get_call(lo, node, span, receiver, attr == "get_nowait")
    if attr == "task_done":
        lo._reject_keywords(node)
        _no_args(lo, node, f"{cls}.task_done")
        if simple:
            lo.fail(node, "PS2001", "SimpleQueue has no task_done")
        return call(_t_leaf("none"), "ps_queue_task_done", [], f"{cls}.task_done")
    if attr == "join":
        lo._reject_keywords(node)
        _no_args(lo, node, f"{cls}.join")
        if simple:
            lo.fail(node, "PS2001", "SimpleQueue has no join")
        return call(_t_leaf("none"), "ps_queue_join", [], f"{cls}.join")
    lo.fail(node, "PS2001", f"{cls}.{attr} is not lowered")
    raise AssertionError("unreachable")


def _put_call(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any], nowait: bool) -> dict[str, Any]:
    """`put(item, block=True, timeout=None)` and `put_nowait(item)`.

    The item travels as a pointer to a stashed slot, the shape
    `deque.append` uses: the queue stores element-sized slots.
    """
    block = True
    timeout: ast.expr | None = None
    if nowait:
        lo._reject_keywords(node)
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "put_nowait expects 1 argument(s)")
    else:
        for keyword in node.keywords:
            if keyword.arg == "block":
                block = _keyword_bool(lo, keyword.value, "block")
            elif keyword.arg == "timeout":
                timeout = keyword.value
            else:
                lo.fail(node, "PS2003", f"put has no keyword argument {keyword.arg}")
        if len(node.args) > 2:
            lo.fail(node, "PS2003", "put expects at most 2 argument(s)")
        if not node.args:
            lo.fail(node, "PS2003", "put expects 1 argument(s)")
        if len(node.args) >= 2:
            block = _keyword_bool(lo, node.args[1], "block")
    if nowait:
        block = False
    if not block and timeout is not None:
        lo.fail(node, "PS1003", "'block' must be true when a timeout is given")
    item = lo._expr(node.args[0])
    wait = _const_int(0, span) if not block else _timeout_nanos(lo, timeout, span)
    return lo._abi_call("queue", "put", [receiver, wait, item], _t_leaf("none"), "ps_queue_put", span)


def _get_call(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any], nowait: bool) -> dict[str, Any]:
    """`get(block=True, timeout=None)` and `get_nowait()`.

    The element type comes from the queue's annotation, like a deque pop.
    """
    block = True
    timeout: ast.expr | None = None
    if nowait:
        lo._reject_keywords(node)
        if node.args:
            lo.fail(node, "PS2003", "get_nowait expects 0 argument(s)")
        block = False
    else:
        for keyword in node.keywords:
            if keyword.arg == "block":
                block = _keyword_bool(lo, keyword.value, "block")
            elif keyword.arg == "timeout":
                timeout = keyword.value
            else:
                lo.fail(node, "PS2003", f"get has no keyword argument {keyword.arg}")
        if len(node.args) > 2:
            lo.fail(node, "PS2003", "get expects at most 2 argument(s)")
        if len(node.args) >= 1:
            block = _keyword_bool(lo, node.args[0], "block")
    if not block and timeout is not None:
        lo.fail(node, "PS1003", "'block' must be true when a timeout is given")
    wait = _const_int(0, span) if not block else _timeout_nanos(lo, timeout, span)
    typ = _node_type(receiver)
    if "elem" not in typ:
        lo.fail(node, "PS2002", "this queue has no element type")
    return lo._abi_call("queue", "get", [receiver, wait], typ["elem"], "ps_queue_get", span)
