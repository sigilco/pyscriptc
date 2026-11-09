"""random call lowering."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import (
    _as_float,
    _const_int,
    _len_of,
    _node_type,
    _node_type_kind,
    _sub,
    _t_leaf,
    _type_kind,
)


def _random_call(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """random lowers onto a single shared MT19937 in ps_rand.c.

    Seed parity with CPython is kept for int seeds (init_by_array on the
    little-endian limbs); seed() with no argument is nondeterministic.
    """
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
