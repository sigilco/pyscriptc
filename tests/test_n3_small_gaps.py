# SPDX-License-Identifier: Apache-2.0
"""The N3 small-gaps batch (issue #1314): enum-as-int, bool fields and
one-field tuples lower instead of fencing.

Each gap was a `PS2002`/`PS1001` fence the design note's rank-3 batch
removes: an `IntEnum` class annotates as `int` and its members take part
in arithmetic (`class C(int, Enum)` included), an enum body with no
member is a base another enum extends, a class field annotates `bool`,
and a tuple literal or annotation spells one field. Every probe diffs
stdout against the CPython oracle.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, run_cli, write_case


def _oracle(tmp_path: Path, name: str, source: str, func: str = "run") -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func), got.stdout


def test_intenum_member_is_an_int(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "member_int.py",
        "\n".join(
            [
                "from enum import IntEnum",
                "",
                "class State(IntEnum):",
                "    OFF = 1",
                "    ON = 2",
                "",
                "def run() -> int:",
                "    x: int = State.ON",
                "    return State.OFF + State.ON + x",
                "",
            ]
        ),
    )


def test_intenum_annotation_takes_an_int(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "annotation_int.py",
        "\n".join(
            [
                "from enum import IntEnum",
                "",
                "class State(IntEnum):",
                "    OFF = 1",
                "    ON = 2",
                "",
                "def total(value: State) -> int:",
                "    return value + 1",
                "",
                "def run() -> int:",
                "    return total(State.ON)",
                "",
            ]
        ),
    )


def test_int_enum_mixin_spelling(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "mixin_int.py",
        "\n".join(
            [
                "from enum import Enum",
                "",
                "class Color(int, Enum):",
                "    RED = 1",
                "    GREEN = 2",
                "",
                "def run() -> int:",
                "    return Color.RED + Color.GREEN",
                "",
            ]
        ),
    )


def test_enum_subclass_of_an_empty_base(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "subclass.py",
        "\n".join(
            [
                "from enum import IntEnum, auto",
                "",
                "class Level(IntEnum):",
                "    pass",
                "",
                "class State(Level):",
                "    DEBUG = auto()",
                "    INFO = auto()",
                "",
                "def run() -> int:",
                "    return State.DEBUG + State.INFO",
                "",
            ]
        ),
    )


def test_plain_enum_keeps_its_own_type(tmp_path: Path) -> None:
    """A plain `Enum` member is not an int: the annotation stays the class.

    The enum subset already lowers a plain enum's members as int constants
    (the divergence the subset carries), but the *type* the annotation
    names is still the enum class, so a slot typed `C` refuses the int
    constant. The change in this issue touches the integer enum only.
    """
    path = write_case(
        tmp_path,
        "plain_enum.py",
        "\n".join(
            [
                "from enum import Enum",
                "",
                "class C(Enum):",
                "    A = 1",
                "",
                "def run() -> int:",
                "    x: C = C.A",
                "    return 0",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "plain.ll"))
    assert got.returncode == 3, got.stderr
    assert "annotation says C" in got.stderr


def test_enum_with_members_is_not_a_base(tmp_path: Path) -> None:
    """A base with members keeps the fence: CPython refuses that subclass."""
    path = write_case(
        tmp_path,
        "member_base.py",
        "\n".join(
            [
                "from enum import IntEnum",
                "",
                "class State(IntEnum):",
                "    OFF = 1",
                "",
                "class Sub(State):",
                "    ON = 2",
                "",
                "def run() -> int:",
                "    return 0",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "base.ll"))
    assert got.returncode == 3, got.stderr
    assert "an enum is not a base" in got.stderr


def test_empty_enum_with_no_subclass_fences(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "empty_enum.py",
        "\n".join(
            [
                "from enum import IntEnum",
                "",
                "class E(IntEnum):",
                "    pass",
                "",
                "def run() -> int:",
                "    return 0",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "empty.ll"))
    assert got.returncode == 3, got.stderr
    assert "an enum needs a member" in got.stderr


def test_bare_int_base_still_fences(tmp_path: Path) -> None:
    """The int erase applies only to the enum mixin spelling: a plain
    `class C(int)` keeps the `this class base is not lowered` fence it
    had before the batch (CPython allows the subclass; the subset does
    not lower an int base outside the enum spelling)."""
    path = write_case(
        tmp_path,
        "int_base.py",
        "\n".join(
            [
                "class MyInt(int):",
                "    pass",
                "",
                "def run() -> int:",
                "    return 0",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "ib.ll"))
    assert got.returncode == 3, got.stderr
    assert "this class base is not lowered" in got.stderr


def test_bool_class_field(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "bool_field.py",
        "\n".join(
            [
                "class C:",
                "    flag: bool",
                "",
                "    def __init__(self) -> None:",
                "        self.flag = True",
                "",
                "    def get(self) -> bool:",
                "        return self.flag",
                "",
                "def run() -> int:",
                "    if C().get():",
                "        return 1",
                "    return 0",
                "",
            ]
        ),
    )


def test_bool_dataclass_field(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "bool_dataclass.py",
        "\n".join(
            [
                "from dataclasses import dataclass",
                "",
                "",
                "@dataclass",
                "class C:",
                "    flag: bool = True",
                "",
                "",
                "def run() -> int:",
                "    if C().flag:",
                "        return 1",
                "    return 0",
                "",
            ]
        ),
    )


def test_one_field_tuple(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "tuple1.py",
        "\n".join(
            [
                "def make() -> tuple[int]:",
                "    return (7,)",
                "",
                "",
                "def run() -> int:",
                "    t: tuple[int] = make()",
                "    x, = t",
                "    return x + t[0]",
                "",
            ]
        ),
    )


def test_one_field_module_tuple(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "tuple_module.py",
        "\n".join(
            [
                "PAIR = (5,)",
                "",
                "",
                "def run() -> int:",
                "    a, = PAIR",
                "    return a",
                "",
            ]
        ),
    )


def test_empty_tuple_still_fences(tmp_path: Path) -> None:
    path = write_case(tmp_path, "tuple0.py", "def run() -> int:\n    t: tuple[()] = ()\n    return 0\n")
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "t0.ll"))
    assert got.returncode == 3, got.stderr


def test_the_committed_probe_matches_cpython(tmp_path: Path) -> None:
    """The differential probe: the compiled program prints what CPython
    prints (probes/differential/enum_int_mixin.py, the AGENTS.md rule)."""
    import subprocess
    import sys

    probe = Path(__file__).resolve().parents[1] / "probes" / "differential" / "enum_int_mixin.py"
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
