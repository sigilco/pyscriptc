"""Class-body constants (#732): `name = <fold>` and `name: T = <fold>`.

A class-body assignment whose value folds to a compile-time constant is a
class attribute, not an instance field: `C.name` and `obj.name` (including
`self.name` inside methods) read the constant, instance fields still
shadow it, and ancestors' constants resolve through the MRO. Values that
do not fold, annotation mismatches and instance writes keep their fences.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

from pyscriptc.coverage import build_report
from pyscriptc.lower import lower_source


def _fences(source: str) -> list[dict[str, str]]:
    return lower_source(source, "case.py")["fences"]


def test_annotated_and_plain_constants_lower_clean() -> None:
    module = lower_source(
        'class C:\n    LIMIT: int = 3\n    NAME = "x"\n\n\ndef probe() -> int:\n    return C.LIMIT + len(C.NAME)\n',
        "case.py",
    )
    assert module["fences"] == []


def test_reads_via_class_instance_self_and_inheritance() -> None:
    module = lower_source(
        "class Base:\n"
        '    KIND = "base"\n'
        "\n"
        "\n"
        "class C(Base):\n"
        "    LIMIT: int = 3\n"
        "    NEG = -4\n"
        "    EXPR = 1 + 2\n"
        "\n"
        "    def total(self) -> int:\n"
        "        return self.LIMIT + C.NEG + C.EXPR\n"
        "\n"
        "\n"
        "def probe() -> int:\n"
        "    c: C = C()\n"
        "    return c.total() + len(C.KIND) + len(c.KIND)\n",
        "case.py",
    )
    assert module["fences"] == []


def test_non_constant_values_keep_the_ps1204_fence() -> None:
    for stmt in ("x = [1]", "x = {}", "x = (1, 2)", "x = print", "x = y = 3"):
        fences = _fences(f"class C:\n    {stmt}\n")
        assert any(fence["code"] == "PS1204" for fence in fences), (stmt, fences)


def test_an_annotation_mismatch_is_a_type_fence() -> None:
    fences = _fences("class C:\n    x: str = 3\n")
    assert any(fence["code"] == "PS1003" for fence in fences), fences


def test_an_instance_write_to_a_constant_still_fences() -> None:
    fences = _fences("class C:\n    X = 3\n\n\ndef wr(c: C) -> int:\n    c.X = 9\n    return c.X\n")
    assert any(fence["code"] == "PS2001" and "not a field" in fence["message"] for fence in fences), fences


def test_a_dataclass_default_reads_as_a_class_constant() -> None:
    module = lower_source(
        "from dataclasses import dataclass\n"
        "\n"
        "\n"
        "@dataclass\n"
        "class D:\n"
        "    a: int = 7\n"
        '    b: str = "hi"\n'
        "\n"
        "\n"
        "def probe() -> int:\n"
        "    return D.a + len(D.b)\n",
        "case.py",
    )
    assert module["fences"] == []


def test_constants_cross_a_sibling_import(tmp_path: Path) -> None:
    (tmp_path / "defs.py").write_text('class Config:\n    LIMIT: int = 3\n    NAME = "cfg"\n', encoding="utf-8")
    (tmp_path / "__main__.py").write_text(
        "from defs import Config\n\n\nprint(Config.LIMIT, Config.NAME, Config().LIMIT)\n",
        encoding="utf-8",
    )
    report = build_report(tmp_path)
    assert report["fences"] == [], report["fences"]


_PROBE = """
class Base:
    KIND = "base"
    N: int = 1


class C(Base):
    LIMIT: int = 3
    NAME = "x"
    NEG = -4
    EXPR = 1 + 2
    CAT = "a" + "b"
    FLAG = True
    PI = 2.5
    OTHER = Base.N + 1
    OPT: int | None = None

    def describe(self) -> str:
        return f"{self.NAME}:{self.LIMIT}:{C.NEG}:{C.EXPR}"


def probe() -> int:
    print(C.LIMIT, C.NAME, C.NEG, C.EXPR, C.CAT, C.FLAG, C.PI, C.OTHER)
    print(C().LIMIT, C().NAME, C().KIND, C().N, C().OPT is None)
    print(C.OPT is None, C().describe())
    return 0
"""


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_class_constants_match_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "class_constants.py", _PROBE)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


def test_non_constant_class_body_still_fences_the_compile(tmp_path: Path) -> None:
    path = write_case(tmp_path, "fenced.py", "class C:\n    x = [1]\n")
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "fenced.ll"))
    assert got.returncode == 3, got.stdout + got.stderr
    assert "PS1204" in got.stderr
