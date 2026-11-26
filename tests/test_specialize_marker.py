"""Issue #1309: the `@specialize` marker and the specialization report.

`pyscriptc._annotations.specialize` asserts the per-call-site
monomorphization the lowering already performs. The marker is a statement
of intent on the page: a marked definition lowers to exactly the copies it
would lower to undecorated, a marked definition that is not generic fences
`PS1207`, and `compile --specializations` prints the report the lowering's
own instantiation tables built.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pyscriptc.boxed import specialize_marked_definitions, specialize_marked_nodes
from pyscriptc.lower import lower_source
from pyscriptc.lower._specialize import render_specializations

ROOT = Path(__file__).resolve().parents[1]

GENERIC_FN = """
from typing import TypeVar
from pyscriptc._annotations import specialize

T = TypeVar("T")


@specialize
def first(xs: list[T]) -> T:
    return xs[0]


def probe() -> int:
    a: list[int] = [3, 4]
    return first(a)
"""

GENERIC_CLASS = """
from typing import Generic, TypeVar
from pyscriptc._annotations import specialize

T = TypeVar("T")


@specialize
class Box(Generic[T]):
    def __init__(self, v: T) -> None:
        self.v: T = v

    def get(self) -> T:
        return self.v


def probe() -> int:
    b: Box[int] = Box(3)
    return b.get()
"""

NOT_GENERIC = """
from pyscriptc._annotations import specialize


@specialize
def plain(x: int) -> int:
    return x + 1


def probe() -> int:
    return plain(1)
"""

UNMARKED_GENERIC = """
from typing import TypeVar

T = TypeVar("T")


def first(xs: list[T]) -> T:
    return xs[0]


def probe() -> int:
    a: list[int] = [3, 4]
    return first(a)
"""


def _lower(source: str) -> tuple[object, object]:
    lowerer = _lowerer(source)
    module = lowerer.module(ast.parse(source))
    return lowerer, module


def _lowerer(source: str) -> object:
    from pyscriptc.lower import Lowerer

    return Lowerer("case.py", source)


def test_marker_is_found_in_every_spelling() -> None:
    assert specialize_marked_definitions(ast.parse(GENERIC_FN)) == ["first"]
    alias = GENERIC_FN.replace(
        "from pyscriptc._annotations import specialize", "import pyscriptc._annotations as ann"
    ).replace("@specialize", "@ann.specialize")
    assert specialize_marked_definitions(ast.parse(alias)) == ["first"]
    dotted = GENERIC_FN.replace(
        "from pyscriptc._annotations import specialize", "import pyscriptc._annotations"
    ).replace("@specialize", "@pyscriptc._annotations.specialize")
    assert specialize_marked_definitions(ast.parse(dotted)) == ["first"]
    from_alias = GENERIC_FN.replace(
        "from pyscriptc._annotations import specialize", "from pyscriptc._annotations import specialize as spec"
    ).replace("@specialize", "@spec")
    assert specialize_marked_definitions(ast.parse(from_alias)) == ["first"]


def test_a_local_decorator_named_specialize_is_not_the_marker() -> None:
    source = """
def specialize(fn):
    return fn


@specialize
def f(x: int) -> int:
    return x + 1
"""
    assert specialize_marked_definitions(ast.parse(source)) == []
    assert specialize_marked_nodes(ast.parse(source)) == {}


def test_the_marker_does_not_change_the_lowering() -> None:
    marked = lower_source(GENERIC_FN, "case.py")
    plain = lower_source(UNMARKED_GENERIC, "case.py")
    assert marked["fences"] == []
    assert plain["fences"] == []
    marked_names = [fn["name"] for fn in marked["functions"]]
    plain_names = [fn["name"] for fn in plain["functions"]]
    assert marked_names == plain_names


def test_the_report_rows_read_the_instantiation_tables() -> None:
    lowerer, _module = _lower(GENERIC_FN)
    rows = lowerer.specialization_rows
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "first"
    assert row["kind"] == "function"
    assert row["count"] == 1
    assert row["copies"] == [{"name": "first.int", "typevars": [{"typevar": "T", "type": "int"}]}]


def test_the_module_record_carries_the_rows() -> None:
    _lowerer_obj, module = _lower(GENERIC_FN)
    rows = module["specializations"]
    assert rows[0]["name"] == "first"


def test_an_unmarked_module_carries_no_section() -> None:
    _lowerer_obj, module = _lower(UNMARKED_GENERIC)
    assert "specializations" not in module


def test_the_marked_class_reports_its_copies() -> None:
    lowerer, _module = _lower(
        GENERIC_CLASS.replace(
            "def probe() -> int:\n    b: Box[int] = Box(3)\n    return b.get()",
            "def probe() -> int:\n    b: Box[int] = Box(3)\n    s: Box[str] = Box('q')\n    return b.get()",
        )
    )
    row = lowerer.specialization_rows[0]
    assert row["name"] == "Box"
    assert row["kind"] == "class"
    assert row["count"] == 2
    names = [copy["name"] for copy in row["copies"]]
    assert names == ["Box_int", "Box_str"]


def test_a_marked_definition_that_is_not_generic_fences_ps1207() -> None:
    lowerer, module = _lower(NOT_GENERIC)
    assert [(f["code"], f["function"]) for f in module["fences"]] == [("PS1207", "plain")]
    assert "PS1207: @specialize on a definition that is not generic" in _explain()


def test_a_generic_definition_with_no_call_sites_specializes_nowhere() -> None:
    source = """
from typing import TypeVar
from pyscriptc._annotations import specialize

T = TypeVar("T")


@specialize
def unused(xs: list[T]) -> T:
    return xs[0]
"""
    lowerer, module = _lower(source)
    assert module["fences"] == []
    assert lowerer.specialization_rows[0]["count"] == 0


def test_the_report_text_renders_the_rows() -> None:
    lowerer, _module = _lower(GENERIC_FN)
    text = render_specializations(lowerer.specialization_rows)
    assert "first: 1 specialization(s) (line 9)" in text
    assert "first.int [T=int]" in text
    assert "total: 1 emitted copy across 1 marked definition(s)" in text


def test_the_report_text_answers_when_nothing_is_marked() -> None:
    assert render_specializations([]) == "no @specialize definitions in this module"


def test_the_cli_prints_the_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from pyscriptc import cli

    path = tmp_path / "marked.py"
    path.write_text(GENERIC_FN, encoding="utf-8")
    code = cli.main(["compile", str(path), "--specializations", "--skip-check"])
    assert code == 0
    out = capsys.readouterr().out
    assert "first: 1 specialization(s)" in out
    assert "first.int [T=int]" in out


def test_the_cli_reports_the_fence_of_a_non_generic_mark(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from pyscriptc import cli

    path = tmp_path / "plain.py"
    path.write_text(NOT_GENERIC, encoding="utf-8")
    code = cli.main(["compile", str(path), "--specializations", "--skip-check"])
    assert code == 3
    err = capsys.readouterr().err
    assert "PS1207" in err


def _explain() -> str:
    from pyscriptc.diagnostics import explain

    return explain("PS1207") or ""


def test_the_probe_matches_cpython() -> None:
    """The committed differential probe lowers without a fence (the byte
    comparison against CPython runs in the nightly probe lane)."""
    probe = ROOT / "probes" / "differential" / "specialize_marker.py"
    assert probe.is_file()
    module = lower_source(probe.read_text(encoding="utf-8"), str(probe))
    assert module["fences"] == []


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_the_marked_program_matches_cpython(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    probe = ROOT / "probes" / "differential" / "specialize_marker.py"
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(probe),
            "--emit",
            "bin",
            "-o",
            str(tmp_path / "probe"),
            "--run",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert built.returncode == 0, built.stderr
    oracle = subprocess.run([sys.executable, str(probe)], cwd=ROOT, capture_output=True, text=True)
    assert oracle.returncode == built.returncode
    assert oracle.stdout == built.stdout
    assert oracle.stderr == built.stderr
