# SPDX-License-Identifier: Apache-2.0
"""`typing.NamedTuple` as a class base (#1369).

`class P(NamedTuple)` collects the body's annotated fields as the positional
fields of a synthetic tuple class: the annotations fix the field types,
a constant default fills the field at construction, and construction,
keyword filling, field reads, subscripts and unpack reuse the tuple-class
machinery `collections.namedtuple` already lowers through. The base spells
through the erased typing import or the qualified `typing.NamedTuple`.
`_fields`/`_asdict`/`_replace` stay fenced, and the typing name stays a
PS2002 fence when it is used as a value.
"""

from __future__ import annotations

from pyscriptc.lower import lower_source


def _fences(source: str) -> list[dict[str, str]]:
    return lower_source(source, "case.py")["fences"]


def _class(source: str, name: str) -> dict:
    module = lower_source(source, "case.py")
    assert module["fences"] == []
    found = [item for item in module["classes"] if item["name"] == name]
    assert len(found) == 1
    return found[0]


def test_fields_come_from_the_annotations() -> None:
    info = _class(
        "from typing import NamedTuple\nclass P(NamedTuple):\n    x: int\n    y: str\n",
        "P",
    )
    assert [(f["name"], f["type"]["kind"]) for f in info["fields"]] == [("x", "int"), ("y", "str")]


def test_both_base_spellings_declare_the_class() -> None:
    from_import = _class(
        "from typing import NamedTuple\nclass P(NamedTuple):\n    x: int\n    y: str\n",
        "P",
    )
    qualified = _class(
        "import typing\nclass P(typing.NamedTuple):\n    x: int\n    y: str\n",
        "P",
    )
    assert from_import["fields"] == qualified["fields"]


def test_typing_forms_of_the_field_annotations() -> None:
    info = _class(
        "from typing import NamedTuple, Optional\nclass P(NamedTuple):\n    xs: list[int]\n    maybe: Optional[int]\n",
        "P",
    )
    kinds = [f["type"]["kind"] for f in info["fields"]]
    assert kinds == ["list", "class"]


def test_an_optional_field_takes_a_bare_none_or_the_element() -> None:
    # The field annotation is the expectation, so `None` boxes into the
    # optional like any parameter, and a bare `str` boxes too. This is the
    # iniconfig shape (`ParsedLine(lineno, section, None, None)`).
    module = lower_source(
        "from typing import NamedTuple\n"
        "class P(NamedTuple):\n"
        "    x: int\n"
        "    y: str | None\n"
        "\n"
        "\n"
        "def probe() -> int:\n"
        "    p: P = P(1, None)\n"
        "    q: P = P(2, 'a')\n"
        "    return 0\n",
        "case.py",
    )
    assert module["fences"] == []


def test_an_optional_field_refuses_a_foreign_type() -> None:
    fences = _fences(
        "from typing import NamedTuple\nclass P(NamedTuple):\n    y: str | None\n\n\ndef probe() -> int:\n"
        "    p: P = P(3)\n    return 0\n"
    )
    assert any(f["code"] == "PS1003" for f in fences)


def test_default_is_a_constant_and_construction_fills_it() -> None:
    module = lower_source(
        "from typing import NamedTuple\n"
        "class P(NamedTuple):\n"
        "    x: int\n"
        "    y: str = 'd'\n"
        "\n"
        "\n"
        "def probe() -> int:\n"
        "    p: P = P(1)\n"
        "    q: P = P(2, 'a')\n"
        "    return 0\n",
        "case.py",
    )
    assert module["fences"] == []


def test_construct_reads_unpack_and_subscript() -> None:
    module = lower_source(
        "from typing import NamedTuple\n"
        "class P(NamedTuple):\n"
        "    x: int\n"
        "    y: str\n"
        "\n"
        "\n"
        "def probe() -> int:\n"
        "    p: P = P(1, 'a')\n"
        "    a, b = p\n"
        "    return p.x + len(p.y) + p[0] + a + len(b) - 3\n",
        "case.py",
    )
    assert module["fences"] == []


def test_annotated_parameters_and_returns() -> None:
    module = lower_source(
        "from typing import NamedTuple\n"
        "class P(NamedTuple):\n"
        "    x: int\n"
        "    y: str\n"
        "\n"
        "\n"
        "def bump(p: P) -> P:\n"
        "    return P(p.x + 1, p.y)\n"
        "\n"
        "\n"
        "def probe() -> int:\n"
        "    return bump(P(1, 'a')).x\n",
        "case.py",
    )
    assert module["fences"] == []


def test_positional_and_keyword_construction_mix() -> None:
    module = lower_source(
        "from typing import NamedTuple\n"
        "class P(NamedTuple):\n"
        "    x: int\n"
        "    y: str\n"
        "\n"
        "\n"
        "def probe() -> int:\n"
        "    p: P = P(x=1, y='a')\n"
        "    q: P = P(2, y='b')\n"
        "    return p.x + q.x\n",
        "case.py",
    )
    assert module["fences"] == []


def test_several_defaults_fill_in_order() -> None:
    module = lower_source(
        "from typing import NamedTuple\n"
        "class P(NamedTuple):\n"
        "    x: int = 1\n"
        "    y: str = 'd'\n"
        "\n"
        "\n"
        "def probe() -> int:\n"
        "    p: P = P()\n"
        "    q: P = P(5)\n"
        "    r: P = P(5, 'e')\n"
        "    return p.x + q.x + r.x\n",
        "case.py",
    )
    assert module["fences"] == []


def test_method_in_the_body_is_a_ps1204_fence() -> None:
    fences = _fences(
        "from typing import NamedTuple\nclass P(NamedTuple):\n    x: int\n    def m(self) -> int:\n        return 1\n"
    )
    assert any(f["code"] == "PS1204" for f in fences)


def test_empty_body_is_a_ps2003_fence() -> None:
    fences = _fences("from typing import NamedTuple\nclass P(NamedTuple):\n    pass\n")
    assert any(f["code"] == "PS2003" for f in fences)


def test_other_base_alongside_namedtuple_fences() -> None:
    fences = _fences(
        "from typing import NamedTuple\nclass Base:\n    pass\n\n\nclass P(Base, NamedTuple):\n    x: int\n"
    )
    assert fences


def test_bool_field_keeps_the_tuple_class_fence() -> None:
    fences = _fences("from typing import NamedTuple\nclass P(NamedTuple):\n    ok: bool\n")
    assert any(f["code"] == "PS2002" for f in fences)


def test_default_order_fence_survives_the_checker_bypass() -> None:
    # CPython rejects a non-default field after a default; the fence here is
    # the tuple class's own field-order rule.
    fences = _fences("from typing import NamedTuple\nclass P(NamedTuple):\n    x: int = 1\n    y: int\n")
    assert fences


def test_fields_member_stays_fenced() -> None:
    module = lower_source(
        "from typing import NamedTuple\n"
        "class P(NamedTuple):\n"
        "    x: int\n"
        "    y: str\n"
        "\n"
        "\n"
        "def probe() -> int:\n"
        "    return len(P._fields)\n",
        "case.py",
    )
    assert module["fences"]


def test_asdict_and_replace_stay_fenced() -> None:
    head = (
        "from typing import NamedTuple\n"
        "class P(NamedTuple):\n"
        "    x: int\n"
        "    y: str\n"
        "\n"
        "\n"
        "def probe() -> int:\n"
        "    p: P = P(1, 'a')\n"
    )
    for tail in ("    return len(p._asdict())\n", "    return p._replace(y='z').x\n"):
        module = lower_source(head + tail, "case.py")
        assert module["fences"], tail


def test_namedtuple_as_a_value_stays_a_ps2002_fence() -> None:
    fences = _fences("from typing import NamedTuple\nP = NamedTuple('P', [('x', int)])\n")
    assert any(f["code"] == "PS2002" for f in fences)
