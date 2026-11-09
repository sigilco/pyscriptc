"""collections constructors."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import (
    _class_elem,
    _const_int,
    _dict_node,
    _flag,
    _node_type,
    _node_type_kind,
    _t_dict,
    _t_leaf,
    _type_kind,
    _type_name,
    _type_value,
)
from pyscriptc.stdmore import slot_size


def _deque(lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
    if len(node.args) > 1:
        lo.fail(node, "PS2003", "deque expects 0 or 1 argument(s)")
    if not node.args:
        if expect is None or _type_name(expect) != "Deque" or "elem" not in expect:
            lo.fail(node, "PS1001", "deque() needs a deque annotation")
        elem = expect["elem"]
        if _type_kind(elem) == "bool":
            lo.fail(node, "PS2002", "list[bool] is not lowered")
        size = _const_int(slot_size(elem), span)
        return lo._abi_call("collections", "deque", [size], expect, "ps_deque_empty", span)
    arg = lo._expr(node.args[0])
    if _node_type_kind(arg) != "list":
        lo.fail(node, "PS1003", "deque() takes a list")
    elem = _node_type(arg)["elem"]
    if _type_kind(elem) == "bool":
        lo.fail(node, "PS2002", "list[bool] is not lowered")
    typ = _class_elem("Deque", elem)
    if expect is not None and _type_name(expect) == "Deque":
        lo._expect(node, typ, expect, "deque")
    size = _const_int(slot_size(elem), span)
    return lo._abi_call("collections", "deque", [size, arg], typ, "ps_deque_from", span)


def _counter(lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
    """`Counter()`, `Counter(str)`, `Counter(list)`, `Counter(dict[K, int])`.

    The result is the map type marked `counter`, so `c[k]` reads 0 on a
    miss instead of raising. Counting happens in ps_coll.c. Subset keeps
    one positional iterable/mapping; `Counter(a=2)` keyword counts,
    `elements`, `most_common`, `subtract`, `total`, and the arithmetic
    operators stay fenced, and `update` keeps its fence because CPython
    adds counts where dict.update replaces.
    """
    if node.keywords:
        lo.fail(node, "PS2001", "Counter keyword arguments are not lowered")
    if len(node.args) > 1:
        lo.fail(node, "PS2003", "Counter expects 0 or 1 argument(s)")
    if not node.args:
        if expect is None or _type_kind(expect) != "dict":
            lo.fail(node, "PS1001", "Counter() needs a Counter annotation")
        return _dict_node({**expect, "counter": True}, span)
    arg = lo._expr(node.args[0])
    at = _node_type(arg)
    if _type_kind(at) == "str":
        typ = _t_dict(_t_leaf("str"), _t_leaf("int"), counter=True)
        return lo._abi_call("collections", "Counter", [arg], typ, "ps_counter_from_buf", span)
    if _type_kind(at) == "list" and not _flag(at, "set") and _type_kind(at["elem"]) in {"int", "str"}:
        typ = _t_dict(at["elem"], _t_leaf("int"), counter=True)
        return lo._abi_call("collections", "Counter", [arg], typ, "ps_counter_from_seq", span)
    if _type_kind(at) == "dict" and _type_kind(_type_value(at)) == "int":
        typ = _t_dict(at["key"], _t_leaf("int"), counter=True)
        return lo._abi_call("collections", "Counter", [arg], typ, "ps_counter_from_map", span)
    lo.fail(node, "PS1003", "Counter takes a str, a list of int or str, or a dict of int values")
    raise AssertionError("unreachable")


def _defaultdict(lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
    """`defaultdict(factory)` where the annotation supplies the value type.

    The factory is validated against the annotation's value type and the
    default itself comes from the type's zero: `defaultdict(list)` makes
    `d[k]` insert a fresh `list`, `defaultdict(int)` inserts 0, and so on.
    Subset factories: list, dict, set, int, float, str. Lambdas and other
    callables, the no-argument form, and `default_factory` access stay
    fenced.
    """
    if node.keywords:
        lo.fail(node, "PS2001", "defaultdict keyword arguments are not lowered")
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "defaultdict expects 1 argument(s)")
    factory = node.args[0]
    factories = {"list", "dict", "set", "int", "float", "str"}
    if not isinstance(factory, ast.Name) or factory.id not in factories:
        lo.fail(node, "PS2001", "defaultdict factory is list, dict, set, int, float, or str")
    name = factory.id
    if expect is None or _type_kind(expect) != "dict":
        lo.fail(node, "PS1001", "defaultdict needs a defaultdict annotation")
    val_t = _type_value(expect)
    ok = (
        (name == "list" and _type_kind(val_t) == "list" and not _flag(val_t, "set"))
        or (name == "set" and _type_kind(val_t) == "list" and _flag(val_t, "set"))
        or (name == "dict" and _type_kind(val_t) == "dict")
        or (name in {"int", "float", "str"} and _type_kind(val_t) == name)
    )
    if not ok:
        lo.fail(node, "PS1003", f"defaultdict({name}) needs a defaultdict annotation whose value is {name}-like")
    return _dict_node({**expect, "defaultdict": True}, span)


def _ordered(lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None) -> dict[str, Any]:
    """`OrderedDict()` with no arguments is the empty dict the compiler already builds.

    A positional iterable and keyword arguments stay fenced. The `ordered` mark
    is not part of the type key, so an annotated `dict` assignment still matches.
    """
    if node.args:
        lo.fail(node, "PS2001", "an OrderedDict iterable is not lowered")
    if node.keywords:
        lo.fail(node, "PS2001", "OrderedDict keyword arguments are not lowered")
    if expect is not None and _type_kind(expect) != "dict":
        lo.fail(node, "PS1001", "OrderedDict needs a dict annotation")
    base = expect if expect is not None else _t_dict(_t_leaf("str"), _t_leaf("int"))
    return _dict_node({**base, "ordered": True}, span)
