"""Checker-facts seam: the mypy adapter answers lowering questions."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from pyscriptc.check import CheckFailed
from pyscriptc.typefacts import (
    CallableFacts,
    FactsProvider,
    MypyFactsAdapter,
    ParameterFacts,
    PyrightFactsAdapter,
    TypeFacts,
    facts_provider,
)

SOURCE = textwrap.dedent(
    """\
    def f(a: int, /, b: str = "x", *args: float, c: bool = True, **kw: int) -> list[int]:
        return [a]

    x: int | None = 1
    y: str | int = "s"
    total = f(1)
    """
)


def _facts(tmp_path: Path, source: str = SOURCE) -> MypyFactsAdapter:
    path = tmp_path / "probe.py"
    path.write_text(source, encoding="utf-8")
    return MypyFactsAdapter().facts(path)


def test_adapter_is_a_facts_provider(tmp_path: Path) -> None:
    adapter = MypyFactsAdapter()
    assert isinstance(adapter, FactsProvider)
    facts = adapter.facts(_write(tmp_path, SOURCE))
    assert facts.provider == "mypy"


def _write(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "probe.py"
    path.write_text(source, encoding="utf-8")
    return path


def test_optional_and_union_members(tmp_path: Path) -> None:
    facts = _facts(tmp_path)
    optional = facts.expression_at(4, 0)  # `x: int | None`
    assert optional is not None
    assert optional.optional is True
    assert optional.kind == "union"
    assert {member.kind for member in optional.args} == {"int", "none"}
    union = facts.expression_at(5, 0)  # `y: str | int`
    assert union is not None
    assert union.kind == "union"
    assert union.optional is False
    assert {member.kind for member in union.args} == {"str", "int"}


def test_generic_args(tmp_path: Path) -> None:
    facts = _facts(tmp_path)
    result = facts.expression_at(6, 8)  # `total = f(1)` → list[int]
    assert result is not None
    assert result.kind == "list"
    assert [arg.kind for arg in result.args] == ["int"]


def test_callable_signature(tmp_path: Path) -> None:
    facts = _facts(tmp_path)
    signature = facts.signature("__main__.f")
    assert signature is not None
    assert signature.positional_only == 1
    assert signature.keyword_only == frozenset({"c"})
    assert signature.defaults == {"b": "x", "c": True}
    assert signature.return_type.kind == "list"
    kinds = [param.kind for param in signature.parameters]
    assert kinds == [
        "positional",
        "positional_optional",
        "varargs",
        "keyword_optional",
        "kwargs",
    ]
    assert signature.parameters[0].type.kind == "int"
    assert signature.parameters[4].type.kind == "int"


def test_overload_choices(tmp_path: Path) -> None:
    source = textwrap.dedent(
        """\
        from typing import overload

        @overload
        def pick(v: int) -> int: ...
        @overload
        def pick(v: str) -> str: ...
        def pick(v: object) -> object:
            return v
        """
    )
    facts = _facts(tmp_path, source)
    name = next(key for key in facts.overloads if key.endswith("pick"))
    variants = facts.overload_choices(name)
    assert len(variants) == 2
    assert {variant.return_type.kind for variant in variants} == {"int", "str"}
    impl = facts.signature(name)
    assert impl is not None


def test_errors_surface_as_check_failed(tmp_path: Path) -> None:
    path = _write(tmp_path, "def f(x: int) -> str:\n    return x\n")
    with pytest.raises(CheckFailed):
        MypyFactsAdapter().facts(path)


def test_facts_provider_dispatch(tmp_path: Path) -> None:
    provider = facts_provider("mypy")
    assert isinstance(provider, MypyFactsAdapter)
    assert isinstance(facts_provider("pyright"), PyrightFactsAdapter)
    with pytest.raises(CheckFailed):
        facts_provider("other")


def test_dataclasses_match_the_plan(tmp_path: Path) -> None:
    # Field set pins the seam so lowering code can rely on it.
    tf = TypeFacts(kind="int")
    assert (tf.optional, tf.args, tf.callable, tf.protocols, tf.runtime_repr) == (
        False,
        (),
        None,
        frozenset(),
        None,
    )
    pf = ParameterFacts(name="a", kind="positional", type=tf)
    cf = CallableFacts(parameters=(pf,), return_type=tf)
    assert cf.positional_only == 0
    assert cf.defaults == {}
