"""Checker-facts seam: provider dispatch, dataclass pins, type-text decoders, pyrefly-tsp semantics."""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from pyscriptc.check import CheckFailed
from pyscriptc.typefacts import (
    CallableFacts,
    FactsProvider,
    ParameterFacts,
    PyreflyFactsAdapter,
    PyreflyTspFactsAdapter,
    TypeFacts,
    _parse_type_text,
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

needs_pyrefly = pytest.mark.skipif(shutil.which("pyrefly") is None, reason="pyrefly binary not on PATH")


def _write(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "probe.py"
    path.write_text(source, encoding="utf-8")
    return path


def _facts(tmp_path: Path, source: str = SOURCE):
    with PyreflyTspFactsAdapter() as adapter:
        return adapter.facts(_write(tmp_path, source))


def test_facts_provider_dispatch() -> None:
    assert isinstance(facts_provider(), PyreflyTspFactsAdapter)
    assert isinstance(facts_provider("pyrefly-tsp"), PyreflyTspFactsAdapter)
    assert isinstance(facts_provider("pyrefly"), PyreflyFactsAdapter)
    assert isinstance(PyreflyTspFactsAdapter(), FactsProvider)
    for removed in ("mypy", "pyright", "tsp", "other"):
        with pytest.raises(CheckFailed):
            facts_provider(removed)


def test_dataclasses_match_the_plan() -> None:
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


@needs_pyrefly
def test_optional_and_union_members(tmp_path: Path) -> None:
    facts = _facts(tmp_path)
    assert facts.provider == "pyrefly-tsp"
    optional = facts.expression_at(4, 0)  # `x: int | None`
    assert optional is not None
    assert optional.optional is True
    assert {member.kind for member in optional.args} >= {"int", "none"}


@needs_pyrefly
def test_callable_signature(tmp_path: Path) -> None:
    facts = _facts(tmp_path)
    signature = facts.signature("__main__.f")
    assert signature is not None
    assert signature.positional_only == 1
    assert signature.keyword_only == frozenset({"c"})
    assert signature.defaults == {"b": "x", "c": True}
    assert signature.return_type.kind == "list"
    assert [param.kind for param in signature.parameters] == [
        "positional",
        "positional_optional",
        "varargs",
        "keyword_optional",
        "kwargs",
    ]


# --- type-text parser ------------------------------------------------------


def test_parse_generic_type() -> None:
    facts = _parse_type_text("list[int]")
    assert facts is not None
    assert facts.kind == "list"
    assert [arg.kind for arg in facts.args] == ["int"]


def test_parse_union_marks_optional() -> None:
    facts = _parse_type_text("int | None")
    assert facts is not None
    assert facts.kind == "union"
    assert facts.optional is True
    assert {arg.kind for arg in facts.args} == {"int", "none"}
    plain = _parse_type_text("str | int")
    assert plain is not None and plain.kind == "union" and not plain.optional


def test_parse_optional_and_union_forms() -> None:
    optional = _parse_type_text("Optional[int]")
    assert optional is not None
    assert optional.optional is True
    assert {arg.kind for arg in optional.args} == {"int", "none"}
    union = _parse_type_text("Union[int, str, None]")
    assert union is not None and union.optional is True


def test_parse_tuple_type() -> None:
    facts = _parse_type_text("tuple[int, str]")
    assert facts is not None
    assert facts.kind == "tuple"
    assert [arg.kind for arg in facts.args] == ["int", "str"]
    homogeneous = _parse_type_text("tuple[int, ...]")
    assert homogeneous is not None and [arg.kind for arg in homogeneous.args] == ["int"]


def test_parse_callable_type() -> None:
    facts = _parse_type_text("Callable[[int, str], bool]")
    assert facts is not None
    assert facts.kind == "callable"
    assert facts.callable is not None
    assert [p.type.kind for p in facts.callable.parameters] == ["int", "str"]
    assert facts.callable.return_type.kind == "bool"


def test_parse_type_typevar_literal() -> None:
    type_fact = _parse_type_text("type[Box]")
    assert type_fact is not None and type_fact.kind == "type" and type_fact.args[0].kind == "Box"
    typevar = _parse_type_text("Self@Box")
    assert typevar is not None and typevar.kind == "typevar"
    literal = _parse_type_text("Literal[1]")
    assert literal is not None and literal.kind == "literal" and literal.args[0].kind == "int"


def test_parse_specials() -> None:
    for text, kind in [("None", "none"), ("Any", "any"), ("Unknown", "any"), ("Never", "unknown")]:
        facts = _parse_type_text(text)
        assert facts is not None and facts.kind == kind, text


def test_parse_nested_generics() -> None:
    facts = _parse_type_text("dict[str, list[int]] | None")
    assert facts is not None and facts.kind == "union" and facts.optional
    member = next(arg for arg in facts.args if arg.kind == "dict")
    assert [arg.kind for arg in member.args] == ["str", "list"]


def test_parse_garbage_degrades() -> None:
    assert _parse_type_text("") is None
    assert _parse_type_text("...") is None
    assert _parse_type_text("foo(1, 2") is None
