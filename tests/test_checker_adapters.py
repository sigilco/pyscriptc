"""Pyrefly/TSP facts adapters: JSON mappers + skip-marked binary smoke tests.

All pure-mapping tests feed captured payloads into the mapper functions —
no subprocess. The smoke tests run the real oracles when the binaries are
reachable (`PYREFLY_BIN`/`pyrefly`, `PYRIGHT_TYPESERVER_BIN`/
`pyright-typeserver`) and skip otherwise.
"""

from __future__ import annotations

import ast
import json
import os
import shlex
import shutil
from pathlib import Path

import pytest

from pyscriptc.check import CheckFailed
from pyscriptc.typefacts import (
    PyreflyFactsAdapter,
    TSPFactsAdapter,
    _def_name_positions,
    _pysa_location_key,
    _pysa_module_facts,
    _pysa_signature,
    _pysa_type,
    _static_defaults,
    _tsp_type,
)


def _class_names(*names: str) -> dict:
    return {
        "classes": [
            {"class": {"module_id": 1, "module_name": "builtins", "class_id": i, "class_name": n}}
            for i, n in enumerate(names)
        ],
        "is_exhaustive": True,
    }


# --- pysa locations ---------------------------------------------------------


def test_pysa_location_key() -> None:
    assert _pysa_location_key("14:13-14:20") == "14:12"
    assert _pysa_location_key("3:5-4:1") == "3:4"
    assert _pysa_location_key("bogus") is None
    assert _pysa_location_key("0:0-0:0") is None


# --- pysa type entries ------------------------------------------------------


def test_pysa_type_scalar() -> None:
    facts = _pysa_type({"string": "int", "is_int": True, "class_names": _class_names("int")})
    assert facts.kind == "int"


def test_pysa_type_generic() -> None:
    facts = _pysa_type({"string": "list[int]", "class_names": _class_names("list")})
    assert facts.kind == "list"
    assert [arg.kind for arg in facts.args] == ["int"]


def test_pysa_type_signature() -> None:
    facts = _pysa_type({"string": "(a: int, b: int = 2) -> int"})
    assert facts.kind == "callable"
    assert facts.callable is not None
    assert [p.name for p in facts.callable.parameters] == ["a", "b"]
    assert facts.callable.parameters[1].kind == "positional_optional"
    assert facts.callable.return_type.kind == "int"


def test_pysa_type_unknown_and_fallback() -> None:
    unknown = _pysa_type({"string": "Unknown"})
    assert unknown.kind == "any"
    # A rendered form outside the grammar falls back to class_names.
    weird = _pysa_type({"string": "???", "class_names": _class_names("Box")})
    assert weird.kind == "Box"
    empty = _pysa_type({})
    assert empty.kind == "unknown"


# --- pysa signatures ---------------------------------------------------------


def _pysa_param(tag: str, name: str, annotation: str, **extra) -> dict:
    param = {"name": name, "annotation": {"string": annotation}}
    param.update(extra)
    return {tag: param}


def test_pysa_signature_param_kinds() -> None:
    signature = {
        "parameters": {
            "List": [
                _pysa_param("PosOnly", "a", "int", required=True),
                _pysa_param("Pos", "b", "str"),
                _pysa_param("VarArg", "args", "float"),
                _pysa_param("KwOnly", "c", "bool"),
                _pysa_param("Kwargs", "kw", "int"),
            ]
        },
        "return_annotation": {"string": "None"},
    }
    facts = _pysa_signature(signature, {"b": "x", "c": True})
    assert facts.positional_only == 1
    kinds = {p.name: p.kind for p in facts.parameters}
    assert kinds == {
        "a": "positional",
        "b": "positional_optional",
        "args": "varargs",
        "c": "keyword_optional",
        "kw": "kwargs",
    }
    assert facts.keyword_only == {"c"}
    assert facts.defaults == {"b": "x", "c": True}
    assert facts.return_type.kind == "none"


# --- pysa module mapping -----------------------------------------------------


_SAMPLE = """\
def add(a: int, b: int = 2) -> int:
    return a + b

class Box:
    def meth(self, x: int) -> str:
        return "y"

nums: list[int] = [1, 2]
total = add(nums[0])
"""


def _fake_pysa_docs() -> tuple[dict, dict]:
    int_t = {"string": "int", "is_int": True, "class_names": _class_names("int")}
    str_t = {"string": "str", "class_names": _class_names("str")}
    list_t = {"string": "list[int]", "class_names": _class_names("list")}
    sig_t = {"string": "(a: int, b: int = 2) -> int"}
    expressions = {
        "format_version": 1,
        "module_id": 1000,
        "module_name": "sample",
        "source_path": {"FileSystem": "/tmp/sample.py"},
        "functions": {
            "MTL": {
                "type_table": [list_t, sig_t, int_t],
                "locations": {"9:19-9:25": 0, "10:9-10:12": 1, "10:13-10:20": 2},
            },
            "F:0": {
                "type_table": [int_t],
                "locations": {"2:12-2:13": 0, "2:16-2:17": 0},
            },
        },
    }
    definitions = {
        "format_version": 1,
        "module_id": 1000,
        "module_name": "sample",
        "source_path": {"FileSystem": "/tmp/sample.py"},
        "function_definitions": {
            "F:0": {
                "name": "add",
                "name_location": "1:5-1:8",
                "parent": "TopLevel",
                "undecorated_signatures": [
                    {
                        "parameters": {
                            "List": [
                                _pysa_param("Pos", "a", "int", required=True),
                                _pysa_param("Pos", "b", "int", required=True),
                            ]
                        },
                        "return_annotation": int_t,
                    }
                ],
            },
            "F:1": {
                "name": "meth",
                "name_location": "5:9-5:13",
                "parent": {"Class": {"class_id": 0}},
                "undecorated_signatures": [
                    {
                        "parameters": {
                            "List": [
                                _pysa_param("Pos", "self", "sample.Box", required=True),
                                _pysa_param("Pos", "x", "int", required=True),
                            ]
                        },
                        "return_annotation": str_t,
                    }
                ],
            },
        },
        "class_definitions": {"0": {"class_id": 0, "name": "Box", "parent": "TopLevel"}},
        "global_variables": {},
    }
    return expressions, definitions


def test_pysa_module_facts(tmp_path: Path) -> None:
    path = tmp_path / "sample.py"
    path.write_text(_SAMPLE)
    tree = ast.parse(_SAMPLE)
    expressions_doc, definitions_doc = _fake_pysa_docs()
    facts = _pysa_module_facts(expressions_doc, definitions_doc, path, tree)
    assert facts.provider == "pyrefly"
    assert facts.expression_at(10, 8).kind == "callable"  # `add` callee
    assert facts.expression_at(10, 12).kind == "int"  # `nums[0]`
    add = facts.signature("__main__.add")
    assert add is not None
    assert [p.name for p in add.parameters] == ["a", "b"]
    assert add.parameters[1].has_default is True  # b = 2 recovered from the AST
    assert add.parameters[1].kind == "positional_optional"
    assert add.return_type.kind == "int"
    meth = facts.signature("__main__.Box.meth")
    assert meth is not None and meth.return_type.kind == "str"


def test_pysa_module_facts_defaults_from_ast(tmp_path: Path) -> None:
    path = tmp_path / "sample.py"
    path.write_text(_SAMPLE)
    tree = ast.parse(_SAMPLE)
    expressions_doc, definitions_doc = _fake_pysa_docs()
    # Mark b defaulted via the AST map: it takes `b = 2` from source.
    facts = _pysa_module_facts(expressions_doc, definitions_doc, path, tree)
    add = facts.signature("__main__.add")
    assert add is not None and add.defaults.get("b") == 2


def test_pysa_format_version_guard(tmp_path: Path) -> None:
    path = tmp_path / "sample.py"
    path.write_text(_SAMPLE)
    tree = ast.parse(_SAMPLE)
    expressions_doc, definitions_doc = _fake_pysa_docs()
    expressions_doc["format_version"] = 99
    with pytest.raises(CheckFailed, match="format_version"):
        _pysa_module_facts(expressions_doc, definitions_doc, path, tree)


def test_def_name_positions() -> None:
    tree = ast.parse(_SAMPLE)
    positions = _def_name_positions(tree)
    assert positions[(1, 4)] == "__main__.add"
    assert positions[(5, 8)] == "__main__.Box.meth"


def test_static_defaults() -> None:
    tree = ast.parse("def f(a: int, b: int = 2, *, c: str = 'x', d: int) -> int:\n    return a\n")
    slots = _static_defaults(tree)["__main__.f"]
    assert slots["b"] == 2 and slots["c"] == "x" and "d" not in slots and "a" not in slots


# --- TSP type mapping --------------------------------------------------------


def _tsp_class(name: str, flags: int = 2, **extra) -> dict:
    decl = {"kind": 0, "category": 6, "node": {"uri": "file:///x.pyi", "range": {}}, "name": name}
    return {"id": 0, "kind": 3, "flags": flags, "declaration": decl, **extra}


def test_tsp_builtin() -> None:
    assert _tsp_type({"id": 0, "kind": 0, "flags": 0, "name": "unknown"}, {}).kind == "unknown"
    assert _tsp_type({"id": 0, "kind": 0, "flags": 0, "name": "any"}, {}).kind == "any"
    assert _tsp_type({"id": 0, "kind": 0, "flags": 0, "name": "never"}, {}).kind == "unknown"


def test_tsp_class_instance_and_type() -> None:
    instance = _tsp_type(_tsp_class("int"), {})
    assert instance.kind == "int"
    instantiable = _tsp_type(_tsp_class("int", flags=1), {})
    assert instantiable.kind == "type" and instantiable.args[0].kind == "int"
    none = _tsp_type(_tsp_class("NoneType"), {})
    assert none.kind == "none" and none.optional


def test_tsp_class_args_literal_tuple() -> None:
    generic = _tsp_type(_tsp_class("list", typeArgs=[_tsp_class("int")]), {})
    assert generic.kind == "list" and [a.kind for a in generic.args] == ["int"]
    literal = _tsp_type(_tsp_class("str", flags=2 | 8, literalValue="hi"), {})
    assert literal.kind == "literal" and literal.args[0].kind == "str"
    tup = _tsp_type(_tsp_class("tuple", typeArgs=[_tsp_class("int"), _tsp_class("str")]), {})
    assert tup.kind == "tuple" and [a.kind for a in tup.args] == ["int", "str"]


def test_tsp_union_function_overloaded_module() -> None:
    union = _tsp_type({"id": 0, "kind": 4, "flags": 0, "subTypes": [_tsp_class("int"), _tsp_class("NoneType")]}, {})
    assert union.kind == "union" and union.optional
    fn = {
        "id": 0,
        "kind": 2,
        "flags": 6,
        "declaration": {"kind": 0, "name": "f"},
        "returnType": _tsp_class("int"),
        "specializedTypes": {"parameterTypes": [_tsp_class("int"), _tsp_class("str")]},
    }
    facts = _tsp_type(fn, {})
    assert facts.kind == "callable"
    assert [p.type.kind for p in facts.callable.parameters] == ["int", "str"]
    assert facts.callable.return_type.kind == "int"
    ovl = _tsp_type({"id": 0, "kind": 7, "flags": 6, "overloads": [fn, fn]}, {})
    assert ovl.kind == "overloaded" and len(ovl.args) == 2
    mod = _tsp_type({"id": 0, "kind": 5, "flags": 0, "moduleName": "os.path"}, {})
    assert mod.kind == "module"
    tvar = _tsp_type({"id": 0, "kind": 6, "flags": 0, "declaration": {"kind": 0, "name": "T"}}, {})
    assert tvar.kind == "typevar" and tvar.runtime_repr == "T"


def test_tsp_typeref_and_synthesized() -> None:
    seen: dict[int, object] = {}
    first = _tsp_type({"id": 7, "kind": 0, "flags": 0, "name": "any"}, seen)
    ref = _tsp_type({"id": 8, "kind": 9, "flags": 0, "typeReferenceId": 7}, seen)
    assert ref is first
    stub = {
        "id": 0,
        "kind": 8,
        "flags": 6,
        "stubContent": "def __init__(self, x: int) -> None: ...",
        "metadata": {"primaryDefinitionOffset": 0},
    }
    facts = _tsp_type(stub, {})
    assert facts.kind == "callable"
    assert facts.callable is not None
    assert [p.name for p in facts.callable.parameters] == ["self", "x"]
    cls = _tsp_type({"id": 0, "kind": 8, "flags": 1, "stubContent": "class Box: ...", "metadata": {}}, {})
    assert cls.kind == "Box"


def test_tsp_garbage() -> None:
    assert _tsp_type(None, {}).kind == "unknown"
    assert _tsp_type({"kind": 99}, {}).kind == "unknown"


# --- real-binary smoke tests ---------------------------------------------------


def _binary(env: str, default: str) -> str | None:
    raw = os.environ.get(env)
    if raw:
        argv = shlex.split(raw)
        if argv and shutil.which(argv[0]):
            return raw
        return None
    return default if shutil.which(default) else None


SMOKE = """\
def add(a: int, b: int = 2) -> int:
    return a + b

nums: list[int] = [1, 2]
total = add(nums[0])
"""


@pytest.mark.skipif(_binary("PYREFLY_BIN", "pyrefly") is None, reason="pyrefly binary not on PATH")
def test_pyrefly_smoke(tmp_path: Path) -> None:
    path = tmp_path / "smoke.py"
    path.write_text(SMOKE)
    facts = PyreflyFactsAdapter().facts(path)
    assert facts.provider == "pyrefly"
    add = facts.signature("__main__.add")
    assert add is not None and add.return_type.kind == "int"
    assert {p.name for p in add.parameters} == {"a", "b"}


@pytest.mark.skipif(
    _binary("PYRIGHT_TYPESERVER_BIN", "pyright-typeserver") is None,
    reason="pyright-typeserver binary not on PATH",
)
def test_tsp_smoke(tmp_path: Path) -> None:
    path = tmp_path / "smoke.py"
    path.write_text(SMOKE)
    with TSPFactsAdapter() as adapter:
        facts = adapter.facts(path)
    assert facts.provider == "tsp"
    add = facts.signature("__main__.add")
    assert add is not None and add.return_type.kind == "int"
    assert facts.expression_at(5, 8) is not None  # the `add` callee
    assert facts.expression_at(5, 12) is not None or facts.expression_at(5, 0) is not None


def _write_report(outdir: Path, kind: str, name: str, source: str | None) -> None:
    doc: dict[str, object] = {"expressions": [], "callables": []}
    if source is not None:
        doc["source_path"] = {"FileSystem": source}
    (outdir / kind).mkdir(parents=True, exist_ok=True)
    (outdir / kind / name).write_text(json.dumps(doc), encoding="utf-8")


def test_report_matches_on_source_path_not_stem(tmp_path: Path) -> None:
    target = tmp_path / "pkg" / "ansi.py"
    target.parent.mkdir()
    target.write_text("x = 1\n", encoding="utf-8")
    outdir = tmp_path / "out"
    # pyrefly names reports by dotted module path; the stem never matches.
    _write_report(outdir, "type_of_expressions", "pkg.ansi:1000.json", str(target.resolve()))
    doc = PyreflyFactsAdapter()._read_report(str(outdir), "type_of_expressions", target.resolve())
    assert doc["source_path"] == {"FileSystem": str(target.resolve())}


def test_report_matches_unknown_module_and_skips_decoys(tmp_path: Path) -> None:
    target = tmp_path / "scratch" / "smoke.py"
    target.parent.mkdir()
    target.write_text("x = 1\n", encoding="utf-8")
    outdir = tmp_path / "out"
    decoy = tmp_path / "elsewhere" / "smoke.py"
    _write_report(outdir, "definitions", "__unknown__:1.json", str(decoy))
    _write_report(outdir, "definitions", "smoke:2.json", str(decoy))
    (outdir / "definitions" / "broken:3.json").write_text("{not json", encoding="utf-8")
    _write_report(outdir, "definitions", "__unknown__:4.json", None)
    _write_report(outdir, "definitions", "__unknown__:5.json", str(target.resolve()))
    doc = PyreflyFactsAdapter()._read_report(str(outdir), "definitions", target.resolve())
    assert doc["source_path"] == {"FileSystem": str(target.resolve())}


def test_report_without_matching_source_path_raises(tmp_path: Path) -> None:
    target = tmp_path / "lonely.py"
    target.write_text("x = 1\n", encoding="utf-8")
    outdir = tmp_path / "out"
    _write_report(outdir, "type_of_expressions", "__unknown__:1.json", str(tmp_path / "other.py"))
    with pytest.raises(CheckFailed, match="emitted no type_of_expressions report"):
        PyreflyFactsAdapter()._read_report(str(outdir), "type_of_expressions", target.resolve())


@pytest.mark.skipif(_binary("PYREFLY_BIN", "pyrefly") is None, reason="pyrefly binary not on PATH")
def test_pyrefly_real_package_module_facts() -> None:
    repo = Path(__file__).resolve().parents[1]
    path = repo / "benchmarks" / "compat" / "top100" / "src" / "colorama" / "ansi.py"
    if not path.is_file():
        pytest.skip("top100 corpus not checked out")
    facts = PyreflyFactsAdapter().facts(path)
    assert len(facts.expressions) == 9
    assert len(facts.callables) == 10
