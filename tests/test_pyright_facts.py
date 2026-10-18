"""Pyright facts oracle: hover-text parser units + langserver smoke test.

`PYRIGHT_LANGSERVER_BIN` picks the oracle command (default
`pyright-langserver` on PATH); the smoke test is skip-marked when the
binary is absent. Everything else exercises the hover-string parsers
with fixtures captured from a real server — no network, no subprocess.
"""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

import pytest
from tests.test_gates import write_case

from pyscriptc.check import CheckFailed
from pyscriptc.typefacts import (
    FactsProvider,
    PyrightFactsAdapter,
    _parse_def_signature,
    _parse_hover_type,
    _parse_type_text,
)

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


# --- signature/hover block parser ------------------------------------------


def test_parse_simple_def_signature() -> None:
    signature = _parse_def_signature("(function) def greet(name: str) -> str")
    assert signature is not None
    assert signature.return_type.kind == "str"
    assert [(p.name, p.kind, p.type.kind) for p in signature.parameters] == [("name", "positional", "str")]


def test_parse_multiline_signature_with_markers() -> None:
    value = (
        "(function) def f(\n"
        "    a: int,\n"
        "    /,\n"
        '    b: str = "x",\n'
        "    *args: float,\n"
        "    c: bool = True,\n"
        "    **kw: int\n"
        ") -> list[int]"
    )
    signature = _parse_def_signature(value)
    assert signature is not None
    assert signature.positional_only == 1
    assert signature.keyword_only == frozenset({"c"})
    assert signature.defaults == {"b": "x", "c": True}
    assert signature.return_type.kind == "list"
    assert [p.kind for p in signature.parameters] == [
        "positional",
        "positional_optional",
        "varargs",
        "keyword_optional",
        "kwargs",
    ]


def test_parse_method_signature_self_typevar() -> None:
    signature = _parse_def_signature("(method) def get(self: Self@Box) -> int")
    assert signature is not None
    assert signature.parameters[0].type.kind == "typevar"
    assert signature.return_type.kind == "int"


def test_parse_hover_variable_decl() -> None:
    facts = _parse_hover_type("(variable) x: int | None")
    assert facts is not None
    assert facts.kind == "union"
    assert facts.optional


def test_parse_hover_function_ref_is_callable() -> None:
    facts = _parse_hover_type("(function) def greet(name: str) -> str")
    assert facts is not None
    assert facts.kind == "callable"
    assert facts.callable is not None and facts.callable.return_type.kind == "str"


def test_parse_hover_class_ref_is_type() -> None:
    facts = _parse_hover_type("class Box()")
    assert facts is not None
    assert facts.kind == "type"
    assert facts.args[0].kind == "Box"


def test_parse_hover_module() -> None:
    facts = _parse_hover_type("(module) typing")
    assert facts is not None and facts.kind == "module"


def test_parse_hover_strips_doc_block() -> None:
    facts = _parse_hover_type("(variable) x: int\n\nSome docstring text.")
    assert facts is not None and facts.kind == "int"


def test_parse_hover_overload_tail_ignored() -> None:
    facts = _parse_hover_type("(function) def pick(v: int) -> int\n(+1 overload)")
    assert facts is not None and facts.kind == "callable"


def test_parse_hover_garbage_degrades() -> None:
    assert _parse_hover_type("") is None
    assert _parse_hover_type("hover text we do not understand") is None


# --- adapter plumbing -------------------------------------------------------


def test_adapter_is_a_facts_provider() -> None:
    assert isinstance(PyrightFactsAdapter(), FactsProvider)
    assert PyrightFactsAdapter().name == "pyright"


def test_adapter_command_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYRIGHT_LANGSERVER_BIN", raising=False)
    assert PyrightFactsAdapter()._argv == ["pyright-langserver", "--stdio"]
    monkeypatch.setenv("PYRIGHT_LANGSERVER_BIN", "node /srv/langserver.js --stdio")
    assert PyrightFactsAdapter()._argv == ["node", "/srv/langserver.js", "--stdio"]


def test_missing_langserver_raises_check_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYRIGHT_LANGSERVER_BIN", "pyright-langserver-does-not-exist-xyz")
    path = write_case(tmp_path, "probe.py", "def probe() -> int:\n    return 1\n")
    adapter = PyrightFactsAdapter()
    with pytest.raises(CheckFailed, match="not installed"):
        adapter.facts(path)


# --- smoke test against the real langserver ---------------------------------


def _langserver_command() -> list[str]:
    raw = os.environ.get("PYRIGHT_LANGSERVER_BIN")
    argv = shlex.split(raw) if raw else ["pyright-langserver"]
    return [*argv, "--stdio"] if "--stdio" not in argv else argv


_langserver_argv = _langserver_command()
_langserver_present = shutil.which(_langserver_argv[0]) is not None or Path(_langserver_argv[0]).exists()


@pytest.mark.skipif(not _langserver_present, reason="pyright langserver not installed")
def test_langserver_facts_smoke(tmp_path: Path) -> None:
    source = """\
def greet(name: str) -> str:
    return "hi " + name

x: int | None = 1
total = greet("w")
"""
    path = write_case(tmp_path, "smoke.py", source)
    with PyrightFactsAdapter() as adapter:
        facts = adapter.facts(path)
    assert facts.provider == "pyright"
    signature = facts.signature("__main__.greet")
    assert signature is not None
    assert signature.return_type.kind == "str"
    declared = facts.expression_at(4, 0)  # `x` gets its declared union, not narrowed
    assert declared is not None and declared.kind == "union" and declared.optional
    call = facts.expression_at(5, 8)  # `greet("w")` records the return type
    assert call is not None and call.kind == "str"


@pytest.mark.skipif(not _langserver_present, reason="pyright langserver not installed")
def test_langserver_overloads_and_methods(tmp_path: Path) -> None:
    source = """\
from typing import overload

@overload
def pick(v: int) -> int: ...
@overload
def pick(v: str) -> str: ...
def pick(v: object) -> object:
    return v

class Box:
    def get(self) -> int:
        return 0
"""
    path = write_case(tmp_path, "ov.py", source)
    with PyrightFactsAdapter() as adapter:
        facts = adapter.facts(path)
    assert facts.signature("__main__.Box.get") is not None
    variants = facts.overload_choices("__main__.pick")
    assert {variant.return_type.kind for variant in variants} == {"int", "str"}
    assert facts.signature("__main__.pick") is not None
