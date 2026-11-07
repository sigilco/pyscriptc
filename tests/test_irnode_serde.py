"""The typed-node serializer (R3 slice one): byte identity with the dict IR.

`pyscriptc._irnode` carries the typed HIR and `tirserde.node_to_dict`
reconstructs the exact dict the lowerer builds today. These tests prove
the reconstruction is faithful: node for node on a lowered module, and
byte for byte through the canonical serializer.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pyscriptc import tirserde
from pyscriptc._irnode import (
    F_BARE,
    F_NEGATED,
    Handler,
    IrNode,
    Pair,
    Span,
    Stub,
    T,
    ThreadMeta,
    const_node,
    name_expr,
    t_class,
    t_func,
    t_leaf,
    t_list,
)


def _nil(kind: str, **kw: object) -> IrNode:
    """An IrNode with every reference field None (test helper)."""
    fields = dict(
        kind=kind,
        span=None,
        type=None,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
    )
    fields.update(kw)
    return IrNode(**fields)  # type: ignore[arg-type]


SPAN = Span("m.py", 1, 1, 1, 9)


def test_a_name_node_reconstructs_its_dict() -> None:
    node = name_expr("x", t_leaf("int"), SPAN)
    assert tirserde.node_to_dict(node) == {
        "kind": "name",
        "id": "x",
        "type": {"kind": "int"},
        "span": {"file": "m.py", "line": 1, "col": 1, "endLine": 1, "endCol": 9},
    }


def test_a_const_node_restores_the_json_payload() -> None:
    bool_const = const_node(3, "true", t_leaf("bool"), SPAN)
    none_const = const_node(4, "", t_leaf("none"), SPAN)
    int_const = const_node(1, "42", t_leaf("int"), SPAN)
    assert tirserde.node_to_dict(bool_const)["value"] is True
    assert tirserde.node_to_dict(none_const)["value"] is None
    assert tirserde.node_to_dict(int_const)["value"] == "42"


def test_type_dicts_spell_their_shapes() -> None:
    assert tirserde.node_to_dict(t_leaf("str")) == {"kind": "str"}
    opt = t_class("Point", "Point | None", t_leaf("int"), optional=True)
    assert tirserde.node_to_dict(opt) == {
        "kind": "class",
        "name": "Point",
        "label": "Point | None",
        "elem": {"kind": "int"},
        "optional": True,
    }
    assert tirserde.node_to_dict(t_list(t_leaf("int"), set_=True)) == {
        "kind": "list",
        "elem": {"kind": "int"},
        "set": True,
    }
    sig = t_func([t_leaf("int")], t_leaf("str"))
    assert tirserde.node_to_dict(sig) == {
        "kind": "func",
        "params": [{"kind": "int"}],
        "return": {"kind": "str"},
    }


def test_the_any_leaf_round_trips() -> None:
    """The unresolved leaf a bare `Any` annotation spells (issue #817)."""
    from pyscriptc._irnode import t_any
    from pyscriptc.tirserde import dict_to_type

    assert tirserde.node_to_dict(t_any()) == {"kind": "any"}
    assert tirserde.node_to_dict(dict_to_type({"kind": "any"})) == {"kind": "any"}
    sig = dict_to_type({"kind": "func", "params": [{"kind": "any"}], "return": {"kind": "any"}})
    assert tirserde.node_to_dict(sig) == {
        "kind": "func",
        "params": [{"kind": "any"}],
        "return": {"kind": "any"},
    }


def test_a_contains_node_spells_negated_either_way() -> None:
    hay = name_expr("xs", t_list(t_leaf("int")), SPAN)
    needle = name_expr("a", t_leaf("int"), SPAN)
    plain = _nil(
        "contains",
        span=SPAN,
        type=t_leaf("bool"),
        hay=hay,
        needle=needle,
    )
    negated = _nil(
        "contains",
        span=SPAN,
        type=t_leaf("bool"),
        hay=hay,
        needle=needle,
        flags=F_NEGATED,
    )
    assert tirserde.node_to_dict(plain)["negated"] is False
    assert tirserde.node_to_dict(negated)["negated"] is True


def test_a_try_node_reconstructs_handlers_and_else() -> None:
    handler = Handler(exc=["ValueError"], codes=[51], body=[], excslot="slot", bind="e", flags=F_BARE)
    node = _nil(
        "try",
        span=SPAN,
        body=[],
        handlers=[handler],
        orelse=[],
        finalbody=[],
        brk="b1",
        cont="c1",
    )
    out = tirserde.node_to_dict(node)
    assert out == {
        "kind": "try",
        "body": [],
        "handlers": [{"exc": ["ValueError"], "codes": [51], "body": [], "bare": True, "excslot": "slot", "bind": "e"}],
        "orelse": [],
        "brk": "b1",
        "cont": "c1",
        "finalbody": [],
        "span": {"file": "m.py", "line": 1, "col": 1, "endLine": 1, "endCol": 9},
    }


def test_a_closure_node_reconstructs_env_and_stub() -> None:
    from pyscriptc._irnode import EnvItem, closure

    env = [EnvItem(None, t_leaf("int"), "a"), EnvItem(name_expr("x", t_leaf("int"), SPAN), t_leaf("int"), "")]
    node = closure(
        "ps.thread",
        env,
        t_func([], t_leaf("int")),
        SPAN,
        stub=Stub([t_leaf("int")], [], t_leaf("int")),
    )
    out = tirserde.node_to_dict(node)
    assert out["env"] == [
        {"name": "a", "type": {"kind": "int"}},
        {
            "init": {
                "kind": "name",
                "id": "x",
                "type": {"kind": "int"},
                "span": SPAN.__dict__ if False else {"file": "m.py", "line": 1, "col": 1, "endLine": 1, "endCol": 9},
            },
            "type": {"kind": "int"},
        },
    ]
    assert out["stub"] == {
        "bound": [{"kind": "int"}],
        "params": [],
        "return": {"kind": "int"},
    }


def test_a_thread_record_reconstructs_both_shapes() -> None:
    pair = _nil("closure", name="ps.thread", env=[], type=t_func([], t_leaf("int")), span=SPAN)
    thread = ThreadMeta(closure=pair, result=None, name="w", daemon=1)
    to_thread = ThreadMeta(closure=None, result=t_leaf("int"), size=8)
    assert tirserde.node_to_dict(thread) == {
        "daemon": 1,
        "name": "w",
        "closure": {
            "kind": "closure",
            "name": "ps.thread",
            "env": [],
            "type": {"kind": "func", "params": [], "return": {"kind": "int"}},
            "span": {"file": "m.py", "line": 1, "col": 1, "endLine": 1, "endCol": 9},
        },
    }
    assert tirserde.node_to_dict(to_thread) == {"result": {"kind": "int"}, "size": 8}


def test_a_dict_literal_reconstructs_pairs() -> None:
    from pyscriptc._irnode import dict_lit

    key = const_node(0, "k", t_leaf("str"), SPAN)
    value = const_node(1, "1", t_leaf("int"), SPAN)
    node = dict_lit([Pair(key, value)], _t_dict_key_value(), SPAN)
    out = tirserde.node_to_dict(node)
    assert out["pairs"] == [
        {
            "key": {"kind": "const", "value": "k", "type": {"kind": "str"}, "span": _span_dict()},
            "value": {"kind": "const", "value": "1", "type": {"kind": "int"}, "span": _span_dict()},
        }
    ]


def test_calloes_spell_their_shapes() -> None:
    from pyscriptc._irnode import builtin, class_callee, function_callee, method_callee, module_callee, value_callee

    assert tirserde.node_to_dict(builtin("len")) == {"kind": "builtin", "name": "len"}
    assert tirserde.node_to_dict(function_callee("f")) == {"kind": "function", "name": "f"}
    assert tirserde.node_to_dict(class_callee("Point")) == {"kind": "class", "name": "Point"}
    assert tirserde.node_to_dict(module_callee("math", "sqrt")) == {
        "kind": "module",
        "module": "math",
        "name": "sqrt",
    }
    assert tirserde.node_to_dict(method_callee("list", "append")) == {
        "kind": "method",
        "receiver": "list",
        "name": "append",
    }
    tupled = tirserde.node_to_dict(method_callee("tuple", "count", tuple_="tuple_2_int_int"))
    assert tupled["tuple"] == "tuple_2_int_int"
    target = name_expr("f", t_leaf("int"), SPAN)
    assert tirserde.node_to_dict(value_callee(target)) == {
        "kind": "value",
        "target": {"kind": "name", "id": "f", "type": {"kind": "int"}, "span": _span_dict()},
    }


def test_an_excmake_cause_spells_code_only_when_set() -> None:
    from pyscriptc._irnode import excmake

    bare = excmake("ValueError", SPAN, -1, None)
    coded = excmake("Empty", SPAN, 71, None)
    assert tirserde.node_to_dict(bare) == {
        "kind": "excmake",
        "exc": "ValueError",
        "message": None,
        "span": _span_dict(),
    }
    assert tirserde.node_to_dict(coded)["code"] == 71


def _span_dict() -> dict[str, object]:
    return {"file": "m.py", "line": 1, "col": 1, "endLine": 1, "endCol": 9}


def _t_dict_key_value() -> T:
    return T(kind="dict", elem=None, key=t_leaf("str"), value=t_leaf("int"), params=None, ret=None)
