# SPDX-License-Identifier: Apache-2.0
"""The per-class demotion plans for the class-base wall (issue #1312)."""

from __future__ import annotations

import ast

from pyscriptc.boxed import (
    BASE_KIND_CASCADE,
    BASE_KIND_ENUM,
    BASE_KIND_ISLAND,
    BASE_KIND_STDLIB,
    BASE_KIND_TYPING,
    BASE_REFUSAL_MESSAGE,
    class_base_demotions,
)

BASE_REFUSAL = {"code": "PS2002", "message": BASE_REFUSAL_MESSAGE}


def _plans(src: str, fences: list[dict]) -> list[dict]:
    return class_base_demotions(ast.parse(src), fences)


def test_unknown_base_plans_island_defined() -> None:
    src = "class Broken(Missing):\n    pass\n"
    plans = _plans(src, [BASE_REFUSAL | {"line": 1}])
    assert len(plans) == 1
    assert plans[0]["class"] == "Broken"
    assert plans[0]["base"] == "Missing"
    assert plans[0]["baseKind"] == BASE_KIND_ISLAND
    assert plans[0]["line"] == 1
    assert "demote class Broken to the island" in plans[0]["plan"]


def test_local_base_plans_cascade() -> None:
    src = "class Base:\n    pass\n\n\nclass Sub(Base):\n    pass\n"
    plans = _plans(src, [BASE_REFUSAL | {"line": 5}])
    assert len(plans) == 1
    assert plans[0]["class"] == "Sub"
    assert plans[0]["base"] == "Base"
    assert plans[0]["baseKind"] == BASE_KIND_CASCADE


def test_typing_base_plans_typing() -> None:
    src = "from typing import NamedTuple\n\n\nclass P(NamedTuple):\n    x: int\n"
    plans = _plans(src, [BASE_REFUSAL | {"line": 4}])
    assert plans[0]["baseKind"] == BASE_KIND_TYPING
    assert plans[0]["base"] == "NamedTuple"


def test_dotted_and_subscript_base_spellings() -> None:
    tree = ast.parse("class P(mod.Protocol):\n    pass\n")
    plans = class_base_demotions(tree, [BASE_REFUSAL | {"line": 1}])
    assert plans[0]["base"] == "mod.Protocol"
    assert plans[0]["baseKind"] == BASE_KIND_TYPING

    tree2 = ast.parse("class G(Generic[T]):\n    pass\n")
    plans2 = class_base_demotions(tree2, [BASE_REFUSAL | {"line": 1}])
    assert plans2[0]["base"] == "Generic"
    assert plans2[0]["baseKind"] == BASE_KIND_TYPING


def test_enum_and_stdlib_refusals_plan_their_kinds() -> None:
    src = "class Weird(Color):\n    pass\n"
    plans = _plans(src, [{"code": "PS2002", "message": "an enum is not a base", "line": 1}])
    assert plans[0]["baseKind"] == BASE_KIND_ENUM

    src2 = "class Threading(threading.Thread):\n    pass\n"
    plans2 = _plans(src2, [{"code": "PS2002", "message": "subclassing a stdlib type is not lowered", "line": 1}])
    assert plans2[0]["baseKind"] == BASE_KIND_STDLIB


def test_island_floor_refusal_names_the_demotion() -> None:
    src = "class Sub(Base):\n    pass\n"
    fence = {
        "code": "PS2002",
        "message": "class Base runs on the island floor of its own module and cannot be a native base class: "
        "the subclass joins the island",
        "line": 1,
    }
    plans = _plans(src, [fence])
    assert plans[0]["class"] == "Sub"
    assert plans[0]["base"] == "Base"
    assert plans[0]["baseKind"] == BASE_KIND_ISLAND


def test_non_base_fences_plan_nothing() -> None:
    src = "def f() -> int:\n    return 1\n\n\nclass C:\n    pass\n"
    fences = [
        {"code": "PS2002", "message": "module m is not lowered", "line": 1},
        {"code": "PS1203", "message": "class keywords are not lowered", "line": 4},
    ]
    assert _plans(src, fences) == []


def test_no_fence_no_plan_and_no_tree_no_plan() -> None:
    src = "class C(Missing):\n    pass\n"
    assert _plans(src, []) == []
    assert class_base_demotions(None, [BASE_REFUSAL | {"line": 1}]) == []


def test_plans_are_sorted_by_line() -> None:
    src = "class B(A):\n    pass\n\n\nclass A(Z):\n    pass\n"
    plans = _plans(
        src,
        [
            BASE_REFUSAL | {"line": 1},
            BASE_REFUSAL | {"line": 5},
        ],
    )
    assert [plan["line"] for plan in plans] == [1, 5]
