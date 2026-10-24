"""Runtime reflection names its culprit and points at the island lane (#556)."""

from __future__ import annotations

import pytest
from tests.test_gates import run_cli

from pyscriptc.coverage import family_of
from pyscriptc.lower import lower_source
from pyscriptc.reflection import mechanism_of, routes_to_island

SIGNATURE = "import inspect\n\n\ndef f(fn: int) -> int:\n    return inspect.signature(fn)\n"
SOURCE = "import inspect\n\n\ndef f(fn: int) -> str:\n    return inspect.getsource(fn)\n"
HINTS_IMPORT = "from typing import get_type_hints\n\n\ndef f(fn: int) -> int:\n    return get_type_hints(fn)\n"
HINTS_ATTR = "import typing\n\n\ndef f(fn: int) -> int:\n    return typing.get_type_hints(fn)\n"
ANNOTATIONS = "def f(model: int) -> int:\n    return model.__annotations__\n"
GETATTR = "def f(obj: int, name: str) -> int:\n    return getattr(obj, name)\n"
METACLASS = "class Model(metaclass=type):\n    pass\n"
TYPE_SYNTH = 'def f() -> int:\n    type("X", (), {})\n    return 1\n'
KWARGS_PARAM = "def f(x: int) -> int:\n    def g(**kwargs) -> int:\n        return 1\n\n    return x\n"
KWARGS_CALL = "def g(x: int) -> int:\n    return x\n\n\ndef f(d: dict[str, int]) -> int:\n    return g(**d)\n"


def fence_messages(source: str, name: str = "case.py") -> list[str]:
    return [fence["message"] for fence in lower_source(source, name)["fences"]]


def one_message(source: str) -> str:
    messages = fence_messages(source)
    assert len(messages) == 1, messages
    return messages[0]


@pytest.mark.parametrize(
    ("source", "culprit", "idiom"),
    [
        (SIGNATURE, "runtime reflection (inspect.signature)", "declare the parameters"),
        (SOURCE, "runtime reflection (inspect.getsource)", "inline the logic"),
        (HINTS_IMPORT, "runtime reflection (typing.get_type_hints)", "pass the concrete types"),
        (HINTS_ATTR, "runtime reflection (typing.get_type_hints)", "pass the concrete types"),
        (ANNOTATIONS, "runtime reflection (__annotations__)", "pass the field types"),
        (GETATTR, "runtime reflection (getattr)", "match the name"),
        (METACLASS, "runtime reflection (metaclass)", "PEP 695"),
        (TYPE_SYNTH, "runtime reflection (type())", "PEP 695"),
    ],
)
def test_a_reflection_fence_names_the_culprit_and_the_island_lane(source: str, culprit: str, idiom: str) -> None:
    message = one_message(source)
    assert message.startswith(culprit)
    assert "island lane (requires libpython)" in message
    assert idiom in message
    assert routes_to_island(message)
    assert mechanism_of(message) == culprit.removeprefix("runtime reflection (").removesuffix(")")


def test_annotations_carries_no_fake_table() -> None:
    message = one_message(ANNOTATIONS)
    assert "no annotation table" in message
    assert "a static schema descriptor is not part of this subset" in message


@pytest.mark.parametrize(
    ("source", "idiom"),
    [
        (KWARGS_PARAM, "PEP 692 Unpack[TypedDict]"),
        (KWARGS_CALL, "pass the keyword arguments explicitly"),
    ],
)
def test_keyword_forwarding_names_pep_692_or_the_explicit_form(source: str, idiom: str) -> None:
    messages = [message for message in fence_messages(source) if "keyword forwarding (**kwargs)" in message]
    assert messages
    assert idiom in messages[0]
    assert "island lane (requires libpython)" in messages[0]
    assert family_of("PS2002", messages[0]) == "reflection"
    assert mechanism_of(messages[0]) == "**kwargs"


def test_reflection_fences_have_their_own_family_and_hint() -> None:
    message = one_message(SIGNATURE)
    assert family_of("PS2001", message) == "reflection"
    assert "island lane" in message


def test_explain_names_the_island_lane() -> None:
    got = run_cli("explain", "PS2001")
    assert got.returncode == 0
    assert "island lane" in got.stdout
