"""random call lowering."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc import _irnode as _ir
from pyscriptc.restlower._shared import (
    _as_float,
    _const_int,
    _len_of,
    _mk_span,
    _node_type,
    _node_type_kind,
    _sub,
    _t_leaf,
    _type_kind,
)
from pyscriptc.tirserde import node_to_dict

# attr -> (parameter names, defaults as float text or None for required).
_DRAWS: dict[str, tuple[list[str], list[str | None]]] = {
    "gauss": (["mu", "sigma"], ["0.0", "1.0"]),
    "normalvariate": (["mu", "sigma"], ["0.0", "1.0"]),
    "lognormvariate": (["mu", "sigma"], [None, None]),
    "expovariate": (["lambd"], ["1.0"]),
    "gammavariate": (["alpha", "beta"], [None, None]),
    "betavariate": (["alpha", "beta"], [None, None]),
}


def _float_const(text: str, span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(_ir.const_node(_ir.P_FLOAT, text, _ir.t_leaf("float"), _mk_span(span)))


def _float_args(
    lo: Any, node: ast.Call, span: dict[str, Any], attr: str, names: list[str], defaults: list[str | None]
) -> list[dict[str, Any]]:
    slots = lo._place_nodes(node, names)
    out: list[dict[str, Any]] = []
    for slot, name, default in zip(slots, names, defaults, strict=True):
        if slot is None:
            if default is None:
                lo.fail(node, "PS2003", f"random.{attr} missing argument {name}")
            out.append(_float_const(default, span))
        else:
            out.append(_as_float(lo, node, lo._expr(slot), span, f"random.{attr}"))
    return out


def _population(lo: Any, node: ast.Call, slot: ast.expr | None, attr: str) -> dict[str, Any]:
    if slot is None:
        lo.fail(node, "PS2003", f"random.{attr} expects a population argument")
    assert slot is not None
    pop = lo._expr(slot)
    if _node_type_kind(pop) != "list":
        lo.fail(node, "PS1003", f"random.{attr} takes a list population, not {_node_type_kind(pop)}")
    return pop


def _int_arg(lo: Any, node: ast.Call, slot: ast.expr, what: str) -> dict[str, Any]:
    arg = lo._expr(slot)
    if _node_type_kind(arg) != "int":
        lo.fail(node, "PS1003", f"{what} is int")
    return arg


def _random_more(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any] | None:
    """The distribution draws, randbytes, choices and sample; None when attr is not one of them."""
    if attr in _DRAWS:
        names, defaults = _DRAWS[attr]
        args = _float_args(lo, node, span, attr, names, defaults)
        return lo._abi_call("random", attr, args, _t_leaf("float"), f"ps_rand_{attr}", span)
    if attr == "triangular":
        slots = lo._place_nodes(node, ["low", "high", "mode"])
        low = (
            _float_const("0.0", span)
            if slots[0] is None
            else _as_float(lo, node, lo._expr(slots[0]), span, "random.triangular")
        )
        high = (
            _float_const("1.0", span)
            if slots[1] is None
            else _as_float(lo, node, lo._expr(slots[1]), span, "random.triangular")
        )
        mode_node = slots[2]
        is_none = isinstance(mode_node, ast.Constant) and mode_node.value is None
        if mode_node is None or is_none:
            mode, has = _float_const("0.0", span), _const_int(0, span)
        else:
            mode, has = _as_float(lo, node, lo._expr(mode_node), span, "random.triangular"), _const_int(1, span)
        return lo._abi_call(
            "random", "triangular", [low, high, mode, has], _t_leaf("float"), "ps_rand_triangular", span
        )
    if attr == "randbytes":
        slots = lo._place_nodes(node, ["n"])
        if slots[0] is None:
            lo.fail(node, "PS2003", "random.randbytes expects 1 argument(s)")
        assert slots[0] is not None
        count = _int_arg(lo, node, slots[0], "random.randbytes count")
        return lo._abi_call("random", "randbytes", [count], _t_leaf("bytes"), "ps_rand_bytes", span)
    if attr == "sample":
        slots = lo._place_nodes(node, ["population", "k"])
        pop = _population(lo, node, slots[0], attr)
        if slots[1] is None:
            lo.fail(node, "PS2003", "random.sample expects k")
        assert slots[1] is not None
        k = _int_arg(lo, node, slots[1], "random.sample k")
        return lo._abi_call("random", "sample", [pop, k], _node_type(pop), "ps_rand_sample", span)
    if attr == "choices":
        slots = lo._place_nodes(node, ["population", "weights", "k"])
        if len(node.args) > 1:
            lo.fail(node, "PS2003", "random.choices takes weights and k as keyword arguments")
        pop = _population(lo, node, slots[0], attr)
        k = _const_int(1, span) if slots[2] is None else _int_arg(lo, node, slots[2], "random.choices k")
        if slots[1] is None or (isinstance(slots[1], ast.Constant) and slots[1].value is None):
            return lo._abi_call("random", "choices", [pop, k], _node_type(pop), "ps_rand_choices", span)
        weights = lo._expr(slots[1])
        wt = _node_type(weights)
        if _type_kind(wt) != "list" or _type_kind(wt["elem"]) not in {"int", "float"}:
            lo.fail(node, "PS1003", "random.choices weights is list[int] or list[float]")
        is_float = _const_int(1 if _type_kind(wt["elem"]) == "float" else 0, span)
        return lo._abi_call(
            "random", "choices", [pop, weights, k, is_float], _node_type(pop), "ps_rand_choices_w", span
        )
    return None


def _random_call(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """random lowers onto a single shared MT19937 in ps_rand.c.

    Seed discipline: CPython-compatible Mersenne Twister. An int seed
    (init_by_array on the little-endian limbs) gives CPython's exact stream,
    and every draw below consumes the stream the way CPython 3.14's random.py
    does, so a seeded run is byte-identical. seed() with no argument is
    nondeterministic, as in CPython. The module-level generator is the only
    one: the Random class stays refused.
    """
    more = _random_more(lo, node, span, attr)
    if more is not None:
        return more
    lo._reject_keywords(node)
    if attr == "seed":
        if len(node.args) > 1:
            lo.fail(node, "PS2003", "random.seed expects 0 or 1 argument(s)")
        if not node.args:
            return lo._abi_call("random", "seed", [], _t_leaf("none"), "ps_rand_seed_auto", span)
        arg = lo._expr(node.args[0])
        if _node_type_kind(arg) != "int":
            lo.fail(node, "PS1003", "random.seed argument is int")
        return lo._abi_call("random", "seed", [arg], _t_leaf("none"), "ps_rand_seed", span)
    if attr == "random":
        if node.args:
            lo.fail(node, "PS2003", "random.random expects 0 argument(s)")
        return lo._abi_call("random", "random", [], _t_leaf("float"), "ps_rand_f64", span)
    if attr == "randint":
        if len(node.args) != 2:
            lo.fail(node, "PS2003", "random.randint expects 2 argument(s)")
        args = [lo._expr(arg) for arg in node.args]
        for arg in args:
            if _node_type_kind(arg) != "int":
                lo.fail(node, "PS1003", "random.randint arguments are int")
        return lo._abi_call("random", "randint", args, _t_leaf("int"), "ps_rand_range2", span)
    if attr == "randrange":
        if not 1 <= len(node.args) <= 3:
            lo.fail(node, "PS2003", "random.randrange expects 1 to 3 argument(s)")
        args = [lo._expr(arg) for arg in node.args]
        for arg in args:
            if _node_type_kind(arg) != "int":
                lo.fail(node, "PS1003", "random.randrange arguments are int")
        if len(args) == 1:
            args = [_const_int(0, span), args[0]]
        if len(args) == 2:
            args.append(_const_int(1, span))
        return lo._abi_call("random", "randrange", args, _t_leaf("int"), "ps_rand_range3", span)
    if attr == "uniform":
        if len(node.args) != 2:
            lo.fail(node, "PS2003", "random.uniform expects 2 argument(s)")
        args = [_as_float(lo, node, lo._expr(arg), span, "random.uniform") for arg in node.args]
        return lo._abi_call("random", "uniform", args, _t_leaf("float"), "ps_rand_uniform", span)
    if attr == "getrandbits":
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "random.getrandbits expects 1 argument(s)")
        arg = lo._expr(node.args[0])
        if _node_type_kind(arg) != "int":
            lo.fail(node, "PS1003", "random.getrandbits argument is int")
        return lo._abi_call("random", "getrandbits", [arg], _t_leaf("int"), "ps_rand_bits", span)
    if attr == "choice":
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "random.choice expects 1 argument(s)")
        arg = lo._expr(node.args[0])
        arg_t = _node_type(arg)
        kind = _type_kind(arg_t)
        if kind == "list":
            elem = arg_t["elem"]
        elif kind == "str":
            elem = _t_leaf("str")
        else:
            lo.fail(node, "PS1003", f"random.choice takes a list or str, not {kind}")
        length = _len_of(arg, span)
        index = lo._abi_call("random", "choice", [length], _t_leaf("int"), "ps_rand_index", span)
        return _sub(arg, index, elem, span)
    if attr == "shuffle":
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "random.shuffle expects 1 argument(s)")
        arg = lo._expr(node.args[0])
        if _node_type_kind(arg) != "list":
            lo.fail(node, "PS1003", "random.shuffle takes a list")
        return lo._abi_call("random", "shuffle", [arg], _t_leaf("none"), "ps_seq_shuffle", span)
    lo.fail(node, "PS2001", f"random.{attr} is not in the lowered subset of random")
    raise AssertionError("unreachable")
