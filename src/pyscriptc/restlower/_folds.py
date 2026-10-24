# T2-5 static-fold boundary: `eval(<string-const>)` folds only through
# `ast.literal_eval` with a scalar result; arbitrary eval/exec stay fenced
# (tier-3 dynamic). `hash(<const>)` folds only on int/bool — str/bytes
# hashing is seed-randomized in CPython. `chr` folds only inside the
# Latin-1 literal range (PS2005 boundary); `ord` only on single chars.
# `int` of str/bytes consts keeps the `ps_int_from_bytes` path so the
# subset's i64 fence and underscore rejection survive. `len` of a
# list/dict literal folds only when every element is itself a const (a
# call element's side effect must survive).

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import _const_int, _const_text

_STATIC_MISSING = object()
_PS2005_MESSAGE = "string literals outside Latin-1 are not lowered"


def _static_scalar(expr: dict[str, Any]) -> Any:
    """The Python value of a folded scalar const, or _STATIC_MISSING.

    Covers the shapes the emitter treats as constants: scalar const nodes
    and unary +/- wrapped around a numeric const. Collection literals stay
    missing — `_static_builtin_fold` handles them only where the element
    values do not matter (`len`).
    """

    if expr.get("kind") == "unary" and expr.get("op") in {"-", "+"} and isinstance(expr.get("operand"), dict):
        inner = _static_scalar(expr["operand"])
        if isinstance(inner, bool) or not isinstance(inner, (int, float)):
            return _STATIC_MISSING
        return -inner if expr["op"] == "-" else inner
    if expr.get("kind") != "const":
        return _STATIC_MISSING
    kind = expr.get("type", {}).get("kind")
    value = expr.get("value")
    if kind == "int" and isinstance(value, str):
        body = value[1:] if value.startswith("-") else value
        if body and body.isdigit():
            return int(value)
        return _STATIC_MISSING
    if kind == "float" and isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return _STATIC_MISSING
    if kind == "str" and isinstance(value, str):
        return value
    if kind == "bytes" and isinstance(value, str):
        return value.encode("latin1")
    if kind == "bool":
        return bool(value)
    if kind == "none":
        return None
    return _STATIC_MISSING


def _static_const(value: Any, span: dict[str, Any]) -> dict[str, Any] | None:
    """const node for a Python scalar the subset emits, or None when the
    value has no literal spelling here (non-Latin-1 str, container, ...)."""

    if isinstance(value, bool):
        return {"kind": "const", "value": value, "type": {"kind": "bool"}, "span": span}
    if isinstance(value, int):
        return _const_int(value, span)
    if isinstance(value, float):
        return {"kind": "const", "value": repr(value), "type": {"kind": "float"}, "span": span}
    if isinstance(value, str):
        if any(ord(ch) > 255 for ch in value):
            return None
        return _const_text(value, "str", span)
    if isinstance(value, bytes):
        return _const_text(value.decode("latin1"), "bytes", span)
    if value is None:
        return {"kind": "const", "value": None, "type": {"kind": "none"}, "span": span}
    return None


def _static_builtin_fold(
    name: str, arg: dict[str, Any], span: dict[str, Any], fence: Any = None
) -> dict[str, Any] | None:
    """Tier-2 fold: `name(<const>)` becomes a const node when CPython's
    result is fully determined at lowering.

    Anything the fold cannot reproduce exactly stays unfurled and the
    caller keeps its usual path: `chr` folds only inside the Latin-1
    boundary, `ord` only on single chars, `hash` only on int/bool
    (str/bytes hashing is seed-randomized in CPython and belongs to the
    tier-3 dynamic tier), and `int`/`str`/`repr`/`abs`/`bool`/`len` only
    when Python's conversion cannot raise.

    `fence` (a lowerer carrying `fail`) upgrades the non-Latin-1 `str`
    result from a silent drop to a `PS2005` fence: a fold whose CPython
    answer is a non-Latin-1 string is the same defect as a non-Latin-1
    literal, and dropping it used to make the call fall through to a
    misleading fence (or none). Without a lowerer the drop stands, for
    the fold probes that run with no statement to attach the fence to.
    """

    value = _static_scalar(arg)
    if value is _STATIC_MISSING:
        if name == "len":
            return _static_len(arg, span)
        return None
    result: Any = _STATIC_MISSING
    try:
        if name == "abs" and isinstance(value, (int, float, bool)):
            result = abs(value)
        elif name == "bool":
            result = bool(value)
        elif name == "int" and not isinstance(value, (str, bytes)):
            # int of text stays on the ps_int_from_bytes path, which keeps the
            # subset's i64 fence and underscore rejection — folding it through
            # Python's int() would silently widen the surface.
            result = int(value)
        elif name == "str":
            result = str(value)
        elif name == "repr":
            result = repr(value)
        elif name == "len" and isinstance(value, (str, bytes)):
            result = len(value)
        elif name == "ord" and isinstance(value, str) and len(value) == 1:
            result = ord(value)
        elif name == "chr" and isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 255:
            result = chr(value)
        elif name == "hash" and isinstance(value, (int, bool)):
            result = hash(value)
        elif name == "round" and isinstance(value, int) and not isinstance(value, bool):
            # round(n) on an int is the identity, so the ndigits const folds
            # through and the `round(x, n)` range fence can read it.
            result = value
    except Exception:  # any conversion failure means the fold does not apply
        return None
    if result is _STATIC_MISSING:
        return None
    if fence is not None and isinstance(result, str) and any(ord(ch) > 255 for ch in result):
        # The fold proved the CPython answer is a non-Latin-1 string: the
        # same defect as a non-Latin-1 literal, so it fences the way the
        # literal path does instead of silently dropping the fold.
        fence.fail(arg, "PS2005", _PS2005_MESSAGE)
    return _static_const(result, span)


def _static_len(arg: dict[str, Any], span: dict[str, Any]) -> dict[str, Any] | None:
    """len() of a collection literal folds only when every element is itself
    a const — anything else may be a call whose side effect is the point."""

    if arg.get("kind") == "list" and not arg["type"].get("set"):
        if all(elem.get("kind") == "const" for elem in arg["elems"]):
            return _const_int(len(arg["elems"]), span)
        return None
    if arg.get("kind") == "dict":
        if all(pair["key"].get("kind") == "const" and pair["value"].get("kind") == "const" for pair in arg["pairs"]):
            return _const_int(len(arg["pairs"]), span)
        return None
    return None


def _eval_fold(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any] | None:
    """`eval(<string-const>)`: only the degenerate literal case folds.

    The fold boundary is `ast.literal_eval` — literals and arithmetic on
    numbers only, never arbitrary eval. Anything it cannot prove safe, and
    any non-scalar result, falls through to the generic call fence; general
    `eval`/`exec` stay tier-3 dynamic (CPython island), never tier-2.

    A literal that turns out to hold a non-Latin-1 character fences the
    way the same literal would outside `eval` (a drop here made the call
    fall through to a misleading `eval is not lowered` fence).
    """

    if node.keywords or len(node.args) != 1:
        return None
    arg = lo._expr(node.args[0])
    text = _static_scalar(arg)
    if not isinstance(text, str):
        return None
    try:
        value = ast.literal_eval(text)
    except Exception:  # literal_eval declined: the source is not provably safe
        return None
    if isinstance(value, str) and any(ord(ch) > 255 for ch in value):
        lo.fail(node, "PS2005", _PS2005_MESSAGE)
    return _static_const(value, span)


def _hash_fold(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any] | None:
    """`hash(<const>)` folds only for int/bool: CPython fixes those hashes,
    while str/bytes/tuple hashing is seed-randomized or element-wise, so it
    stays on the fenced path (tier-3 dynamic)."""

    if node.keywords or len(node.args) != 1:
        return None
    arg = lo._expr(node.args[0])
    return _static_builtin_fold("hash", arg, span)
