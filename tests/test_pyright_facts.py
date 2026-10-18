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
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import write_case

from pyscriptc.check import CheckFailed
from pyscriptc.typefacts import (
    _CLI_ERRORS,
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
    monkeypatch.setenv("PYRIGHT_BIN", "pyright-does-not-exist-xyz")  # keep the LSP lane
    path = write_case(tmp_path, "probe.py", "def probe() -> int:\n    return 1\n")
    adapter = PyrightFactsAdapter()
    with pytest.raises(CheckFailed, match="not installed"):
        adapter.facts(path)


# --- `--facts` CLI fast lane --------------------------------------------------

# Fake pyright CLI: --help advertises --facts, a --facts run prints a canned
# report with __FILE__ swapped for the target path.
_FAKE_CLI = """\
import json
import sys

if "--help" in sys.argv:
    print("  --facts   Emit structured type facts (schema pyright-facts) as JSON")
    sys.exit(0)

path = sys.argv[-1]
report = __REPORT__
report["facts"] = {path: report.pop("_doc")}
print(json.dumps(report))
"""

_FACTS_DOC = {
    "file": "__FILE__",
    "expressions": [
        {
            "start": 0,
            "end": 1,
            "line": 0,
            "col": 0,
            "type": {"kind": "class", "name": "int", "fullName": "builtins.int"},
        },
        {
            "start": 4,
            "end": 15,
            "line": 0,
            "col": 3,
            "type": {
                "kind": "union",
                "options": [
                    {"kind": "type", "name": "int", "fullName": "builtins.int"},
                    {"kind": "type", "name": "NoneType", "fullName": "types.NoneType"},
                ],
            },
        },
        {
            "start": 0,
            "end": 5,
            "line": 1,
            "col": 0,
            "type": {"kind": "class", "name": "str", "fullName": "builtins.str"},
        },
    ],
    "callables": {
        "fixture.f": {
            "fullName": "fixture.f",
            "params": [
                {"name": "a", "category": "simple", "type": {"kind": "class", "name": "int"}, "hasDefault": False},
                {"category": "simple", "type": {"kind": "unknown"}, "hasDefault": False},
                {
                    "name": "b",
                    "category": "simple",
                    "type": {"kind": "class", "name": "int"},
                    "defaultType": {"kind": "class", "name": "int", "literalValue": 3},
                    "hasDefault": True,
                },
                {"name": "args", "category": "args", "type": {"kind": "class", "name": "str"}, "hasDefault": False},
                {
                    "name": "c",
                    "category": "simple",
                    "type": {"kind": "class", "name": "bool"},
                    "defaultType": {"kind": "class", "name": "bool", "literalValue": True},
                    "hasDefault": True,
                },
                {"name": "kw", "category": "kwargs", "type": {"kind": "class", "name": "int"}, "hasDefault": False},
            ],
            "returnType": {"kind": "class", "name": "str", "fullName": "builtins.str"},
        },
        "fixture.outer": {
            "fullName": "fixture.outer",
            "params": [
                {"name": "x", "category": "simple", "type": {"kind": "class", "name": "int"}, "hasDefault": False}
            ],
            "returnType": {"kind": "class", "name": "int"},
        },
        "fixture.outer.inner": {
            "fullName": "fixture.outer.inner",
            "params": [
                {"name": "y", "category": "simple", "type": {"kind": "class", "name": "int"}, "hasDefault": False}
            ],
            "returnType": {"kind": "class", "name": "int"},
        },
    },
    "overloads": {
        "fixture.pick": [
            {
                "params": [
                    {"name": "v", "category": "simple", "type": {"kind": "class", "name": "int"}, "hasDefault": False}
                ],
                "returnType": {"kind": "class", "name": "int"},
            },
            {
                "params": [
                    {"name": "v", "category": "simple", "type": {"kind": "class", "name": "str"}, "hasDefault": False}
                ],
                "returnType": {"kind": "class", "name": "str"},
            },
        ],
    },
    "classes": {},
}


def _fake_facts_cli(tmp_path: Path) -> list[str]:
    script = tmp_path / "fake_pyright.py"
    script.write_text(_FAKE_CLI.replace("__REPORT__", repr({"_doc": _FACTS_DOC})), encoding="utf-8")
    return [sys.executable, str(script)]


def test_facts_cli_lane_maps_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYRIGHT_BIN", raising=False)
    source = """\
def outer(x: int) -> int:
    def inner(y: int) -> int:
        return y
    return inner(x)
"""
    path = write_case(tmp_path, "fixture.py", source)
    adapter = PyrightFactsAdapter(cli=_fake_facts_cli(tmp_path))
    assert adapter._facts_cli() == _fake_facts_cli(tmp_path)
    facts = adapter.facts(path)

    assert facts.provider == "pyright"
    assert facts.signature("__main__.outer.inner") is None  # nested names keep <locals>
    assert facts.signature("__main__.outer.<locals>.inner") is not None

    f_sig = facts.signature("__main__.f")
    assert f_sig is not None
    assert f_sig.positional_only == 1
    assert f_sig.keyword_only == frozenset({"c"})
    assert f_sig.defaults == {"b": 3, "c": True}
    assert [p.kind for p in f_sig.parameters] == [
        "positional",
        "positional_optional",
        "varargs",
        "keyword_optional",
        "kwargs",
    ]
    assert f_sig.return_type.kind == "str"

    variants = facts.overload_choices("__main__.pick")
    assert {v.return_type.kind for v in variants} == {"int", "str"}

    # Expression at "1:0" is the union fact moved onto the AnnAssign
    # target only when ast+pyright positions line up; the fixture's
    # expressions land on arbitrary keys — just check shape.
    assert facts.expressions
    assert all(t.kind for t in facts.expressions.values())


def test_facts_cli_denoted_annotation(tmp_path: Path) -> None:
    """The AnnAssign override unwraps `type[X]` to the declared X."""
    source = "x: int | None = 1\n"
    path = write_case(tmp_path, "decl.py", source)
    adapter = PyrightFactsAdapter(cli=_fake_facts_cli(tmp_path))
    facts = adapter.facts(path)
    # The fixture's expression at (0,0) is a bare `int` class — the target
    # override copies the annotation fact (0,4 union of type[]) denoted.
    target = facts.expression_at(1, 0)
    assert target is not None
    assert target.kind == "union" and target.optional
    assert {arg.kind for arg in target.args} == {"int", "none"}


def test_facts_cli_probe_rejects_stock_pyright(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYRIGHT_BIN", raising=False)
    script = tmp_path / "stock_pyright.py"
    script.write_text("import sys\nprint('usage: pyright [options]')\nsys.exit(0)\n", encoding="utf-8")
    adapter = PyrightFactsAdapter(cli=[sys.executable, str(script)])
    assert adapter._facts_cli() is None


def _facts_cli_present() -> bool:
    argv = shlex.split(os.environ.get("PYRIGHT_BIN", "pyright"))
    if not argv or shutil.which(argv[0]) is None:
        return False
    try:
        proc = subprocess.run([*argv, "--help"], capture_output=True, text=True, timeout=30)
    except _CLI_ERRORS:
        return False
    return "--facts" in (proc.stdout or "")


_facts_cli_present = _facts_cli_present()


@pytest.mark.skipif(not _facts_cli_present, reason="no --facts-capable pyright CLI on PYRIGHT_BIN/PATH")
def test_facts_cli_live_smoke(tmp_path: Path) -> None:
    source = """\
def greet(name: str) -> str:
    return "hi " + name

x: int | None = 1
total = greet("w")
"""
    path = write_case(tmp_path, "smoke.py", source)
    adapter = PyrightFactsAdapter()
    facts = adapter.facts(path)
    assert facts.provider == "pyright"
    signature = facts.signature("__main__.greet")
    assert signature is not None and signature.return_type.kind == "str"
    declared = facts.expression_at(4, 0)
    assert declared is not None and declared.kind == "union" and declared.optional
    call = facts.expression_at(5, 8)
    assert call is not None and call.kind == "str"


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
