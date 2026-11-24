# SPDX-License-Identifier: Apache-2.0
"""The classmethod class id (issue #447, divergence 10).

`Class.method(...)` and `instance.method(...)` deliver the defining class as
the compile-time class id: the body's `cls` reads the id, `cls.attr` resolves
against that class at compile time, `cls.method(...)` and `cls(...)` are
static calls, and `cls` as a value keeps its fence.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import compile_run, run_cli, write_case

from pyscriptc.classids import ClassIdTable
from pyscriptc.lower import lower_source


def test_classmethod_call_and_cls_reads_lower_clean() -> None:
    module = lower_source(
        "class K:\n"
        "    total: int = 3\n"
        "\n"
        "    @classmethod\n"
        "    def get(cls) -> int:\n"
        "        return cls.total\n"
        "\n"
        "    @classmethod\n"
        "    def pair(cls) -> int:\n"
        "        return cls.get() + K.total\n"
        "\n"
        "\n"
        "def probe() -> int:\n"
        "    return K.get() + K.pair()\n",
        "case.py",
    )
    assert module["fences"] == [], module["fences"]


def test_cls_as_a_value_still_fences() -> None:
    module = lower_source(
        "class K:\n    @classmethod\n    def get(cls) -> int:\n        return 0 + cls\n",
        "case.py",
    )
    assert any(fence["code"] == "PS2002" for fence in module["fences"]), module["fences"]


def test_cls_call_constructs_the_receiver() -> None:
    module = lower_source(
        "class K:\n"
        "    n: int\n"
        "\n"
        "    def __init__(self, n: int) -> None:\n"
        "        self.n = n\n"
        "\n"
        "    @classmethod\n"
        "    def make(cls, n: int) -> K:\n"
        "        return cls(n)\n"
        "\n"
        "\n"
        "def probe() -> int:\n"
        "    return K.make(4).n\n",
        "case.py",
    )
    assert module["fences"] == [], module["fences"]


def test_class_id_table_assigns_in_order() -> None:
    table = ClassIdTable()
    assert table.assign("A") == 1
    assert table.assign("B") == 2
    assert table.assign("A") == 1
    assert table.of("C") == 0


def test_the_committed_probe_matches_cpython(tmp_path: Path) -> None:
    """The differential probe: the compiled program prints what CPython
    prints (probes/differential/classmethod_class_id.py, the AGENTS.md rule)."""
    probe = Path(__file__).resolve().parents[1] / "probes" / "differential" / "classmethod_class_id.py"
    out = str(tmp_path / "probe.bin")
    got = run_cli("compile", str(probe), "--skip-check", "--run", "--emit", "bin", "-o", out)
    expected = subprocess.run(
        [sys.executable, str(probe)],
        capture_output=True,
        text=True,
        encoding="latin-1",
        check=False,
    )
    assert (got.returncode, got.stdout, got.stderr) == (
        expected.returncode,
        expected.stdout,
        expected.stderr,
    )


def test_instance_call_and_inherited_classmethod_run(tmp_path: Path) -> None:
    source = (
        "class Base:\n"
        "    total: int = 3\n"
        "\n"
        "    @classmethod\n"
        "    def get(cls) -> int:\n"
        "        return cls.total\n"
        "\n"
        "\n"
        "class Sub(Base):\n"
        "    pass\n"
        "\n"
        "\n"
        "def probe() -> int:\n"
        "    b: Base = Base()\n"
        "    print(Base.get())\n"
        "    print(b.get())\n"
        "    print(Sub.get())\n"
        "    return b.get()\n"
    )
    path = write_case(tmp_path, "cm_inherit.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "3\n3\n3\n3\n", got.stdout
