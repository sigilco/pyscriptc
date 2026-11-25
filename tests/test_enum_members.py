# SPDX-License-Identifier: Apache-2.0
"""The enum member surface (issue #451, phase O5a).

Every CPython enum member carries its value and its name, an enum class
body can spell methods and properties that every member answers, and the
`StrEnum` and flag bases widen the subset's base set. Each probe diffs
stdout against the CPython oracle; the differential probe
`probes/differential/enum_members.py` runs the same program on both sides.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import compile_run, oracle, run_cli, write_case


def _oracle(tmp_path: Path, name: str, source: str, func: str = "run") -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func), got.stdout


def test_enum_member_value_and_name(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "value_name.py",
        "\n".join(
            [
                "from enum import Enum",
                "",
                "class Color(Enum):",
                "    RED = 1",
                "    GREEN = 2",
                "",
                "def run() -> int:",
                "    print(Color.RED.value, Color.RED.name)",
                "    print(Color.GREEN.value, Color.GREEN.name)",
                "    return 0",
                "",
            ]
        ),
    )


def test_intenum_member_value_and_name(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "int_value_name.py",
        "\n".join(
            [
                "from enum import IntEnum, auto",
                "",
                "class State(IntEnum):",
                "    OFF = auto()",
                "    ON = auto()",
                "",
                "def run() -> int:",
                "    print(State.OFF.value, State.OFF.name)",
                "    print(State.ON.value, State.ON.name)",
                "    return 0",
                "",
            ]
        ),
    )


def test_strenum_member_value_and_name(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "str_value_name.py",
        "\n".join(
            [
                "from enum import StrEnum",
                "",
                "class Name(StrEnum):",
                "    FIRST = 'first'",
                "    SECOND = 'second'",
                "",
                "def run() -> int:",
                "    print(Name.FIRST.value, Name.FIRST.name)",
                "    print(Name.SECOND.value, Name.SECOND.name)",
                "    return 0",
                "",
            ]
        ),
    )


def test_strenum_auto_spells_the_lower_case_name(tmp_path: Path) -> None:
    """CPython's StrEnum `auto()` takes the member's name in lower case."""
    _oracle(
        tmp_path,
        "str_auto.py",
        "\n".join(
            [
                "from enum import StrEnum, auto",
                "",
                "class Name(StrEnum):",
                "    FIRST = auto()",
                "    SECOND = auto()",
                "",
                "def run() -> int:",
                "    print(Name.FIRST.value, Name.FIRST.name)",
                "    print(Name.SECOND.value, Name.SECOND.name)",
                "    return 0",
                "",
            ]
        ),
    )


def test_auto_continues_from_the_largest_spelled_value(tmp_path: Path) -> None:
    """CPython's `auto()` continues from the largest value the body spelled.

    `DEBUG = 10` then `auto()` gives 11, and an explicit value after an
    `auto()` moves the counter again (`A = 5`, `auto()` gives 6, `C = 2`,
    `auto()` gives 7), so the counter reads the largest value spelled so
    far, not the most recent one.
    """
    _oracle(
        tmp_path,
        "auto_continues.py",
        "\n".join(
            [
                "from enum import IntEnum, auto",
                "",
                "class Level(IntEnum):",
                "    DEBUG = 10",
                "    INFO = auto()",
                "    WARN = auto()",
                "    ERROR = 40",
                "    CRITICAL = auto()",
                "",
                "class Mixed(IntEnum):",
                "    A = 5",
                "    B = auto()",
                "    C = 2",
                "    D = auto()",
                "",
                "def run() -> int:",
                "    print(Level.DEBUG.value, Level.INFO.value, Level.WARN.value)",
                "    print(Level.ERROR.value, Level.CRITICAL.value)",
                "    print(Mixed.A.value, Mixed.B.value, Mixed.C.value, Mixed.D.value)",
                "    return 0",
                "",
            ]
        ),
    )


def test_strenum_annotation_takes_a_str(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "str_annotation.py",
        "\n".join(
            [
                "from enum import StrEnum",
                "",
                "class Name(StrEnum):",
                "    FIRST = 'first'",
                "",
                "def label(value: Name) -> str:",
                "    return value + '!'",
                "",
                "def run() -> int:",
                "    print(label(Name.FIRST))",
                "    return 0",
                "",
            ]
        ),
    )


def test_enum_method_answers_on_every_member(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "method.py",
        "\n".join(
            [
                "from enum import Enum",
                "",
                "class Color(Enum):",
                "    RED = 1",
                "    GREEN = 2",
                "",
                "    def label(self) -> str:",
                "        return 'lamp'",
                "",
                "def run() -> int:",
                "    print(Color.RED.label())",
                "    print(Color.GREEN.label())",
                "    return 0",
                "",
            ]
        ),
    )


def test_enum_property_answers_on_every_member(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "property.py",
        "\n".join(
            [
                "from enum import Enum",
                "",
                "class Color(Enum):",
                "    RED = 1",
                "    GREEN = 2",
                "",
                "    @property",
                "    def low(self) -> int:",
                "        return 5",
                "",
                "def run() -> int:",
                "    print(Color.RED.low)",
                "    print(Color.GREEN.low)",
                "    return 0",
                "",
            ]
        ),
    )


def test_enum_method_reads_the_member_value(tmp_path: Path) -> None:
    """The method's `self` is the member, and the member is the value."""
    _oracle(
        tmp_path,
        "self_value.py",
        "\n".join(
            [
                "from enum import IntEnum",
                "",
                "class State(IntEnum):",
                "    OFF = 3",
                "    ON = 4",
                "",
                "    def doubled(self) -> int:",
                "        return self * 2",
                "",
                "def run() -> int:",
                "    print(State.OFF.doubled())",
                "    print(State.ON.doubled())",
                "    return 0",
                "",
            ]
        ),
    )


def test_strenum_method_reads_the_member_as_a_str(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "str_method.py",
        "\n".join(
            [
                "from enum import StrEnum",
                "",
                "class Name(StrEnum):",
                "    FIRST = 'first'",
                "",
                "    def shout(self) -> str:",
                "        return self.upper() + '!'",
                "",
                "def run() -> int:",
                "    print(Name.FIRST.shout())",
                "    return 0",
                "",
            ]
        ),
    )


def test_intflag_bit_operators(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "intflag.py",
        "\n".join(
            [
                "from enum import IntFlag",
                "",
                "class Perm(IntFlag):",
                "    READ = 1",
                "    WRITE = 2",
                "    EXEC = 4",
                "",
                "def run() -> int:",
                "    print(Perm.READ | Perm.WRITE)",
                "    print(Perm.EXEC & Perm.READ)",
                "    print(Perm.READ & Perm.READ)",
                "    print(Perm.READ | Perm.WRITE | Perm.EXEC)",
                "    return 0",
                "",
            ]
        ),
    )


def test_intflag_annotation_takes_an_int(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "intflag_annotation.py",
        "\n".join(
            [
                "from enum import IntFlag",
                "",
                "class Perm(IntFlag):",
                "    READ = 1",
                "    WRITE = 2",
                "",
                "def count(value: Perm) -> int:",
                "    return value + 1",
                "",
                "def run() -> int:",
                "    print(count(Perm.READ))",
                "    return 0",
                "",
            ]
        ),
    )


def test_flag_members_answer_value_and_name(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "flag.py",
        "\n".join(
            [
                "from enum import Flag",
                "",
                "class Bits(Flag):",
                "    A = 1",
                "    B = 2",
                "",
                "def run() -> int:",
                "    print(Bits.A.value, Bits.A.name)",
                "    print(Bits.B.value, Bits.B.name)",
                "    return 0",
                "",
            ]
        ),
    )


def test_strenum_member_must_be_a_string(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "str_member_int.py",
        "\n".join(
            [
                "from enum import StrEnum",
                "",
                "class Bad(StrEnum):",
                "    A = 1",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "bad.ll"))
    assert got.returncode == 3, got.stderr
    assert "a StrEnum member is a string constant" in got.stderr


def test_enum_member_method_that_does_not_exist_fences(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "no_method.py",
        "\n".join(
            [
                "from enum import Enum",
                "",
                "class Color(Enum):",
                "    RED = 1",
                "",
                "def run() -> int:",
                "    print(Color.RED.missing())",
                "    return 0",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "missing.ll"))
    assert got.returncode == 3, got.stderr
    assert "Color.missing is not a method" in got.stderr


def test_the_committed_probe_matches_cpython(tmp_path: Path) -> None:
    """The differential probe: the compiled program prints what CPython
    prints (probes/differential/enum_members.py, the AGENTS.md rule)."""
    probe = Path(__file__).resolve().parents[1] / "probes" / "differential" / "enum_members.py"
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
