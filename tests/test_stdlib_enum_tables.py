"""The enum member tables.

`E["X"]`, `E(v)`, `E.__members__` and `list(E)` read the compile-time
member table the compiler builds from the enum body, so no enum object
exists in the binary. Each probe diffs stdout against the CPython oracle;
a name or value that names no member is a compile-time diagnostic.
"""
from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, run_cli, write_case


def _oracle(tmp_path: Path, name: str, source: str, func: str = "run") -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func), got.stdout


def test_member_lookup_by_name(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "lookup_name.py",
        "\n".join(
            [
                "from enum import IntEnum",
                "",
                "class Color(IntEnum):",
                "    RED = 1",
                "    GREEN = 2",
                "",
                "def run() -> int:",
                "    print(Color['GREEN'])",
                "    return 0",
                "",
            ]
        ),
    )


def test_member_lookup_by_value(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "lookup_value.py",
        "\n".join(
            [
                "from enum import IntEnum",
                "",
                "class Color(IntEnum):",
                "    RED = 1",
                "    GREEN = 2",
                "",
                "def run() -> int:",
                "    print(Color(2))",
                "    return 0",
                "",
            ]
        ),
    )


def test_members_table(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "members.py",
        "\n".join(
            [
                "from enum import IntEnum",
                "",
                "class Color(IntEnum):",
                "    RED = 1",
                "    GREEN = 2",
                "",
                "def run() -> int:",
                "    m = Color.__members__",
                "    print(m['RED'], m['GREEN'])",
                "    return 0",
                "",
            ]
        ),
    )


def test_list_of_members(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "list_members.py",
        "\n".join(
            [
                "from enum import IntEnum",
                "",
                "class Color(IntEnum):",
                "    RED = 1",
                "    GREEN = 2",
                "    BLUE = 3",
                "",
                "def run() -> int:",
                "    for v in list(Color):",
                "        print(v)",
                "    return 0",
                "",
            ]
        ),
    )


def test_unknown_member_name_fences(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "bad_name.py",
        "\n".join(
            [
                "from enum import IntEnum",
                "",
                "class Color(IntEnum):",
                "    RED = 1",
                "",
                "def run() -> int:",
                "    print(Color['BLUE'])",
                "    return 0",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "bad.ll"))
    assert got.returncode == 3, got.stderr
    assert "is not a member of" in got.stderr


def test_unknown_member_value_fences(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "bad_value.py",
        "\n".join(
            [
                "from enum import IntEnum",
                "",
                "class Color(IntEnum):",
                "    RED = 1",
                "",
                "def run() -> int:",
                "    print(Color(9))",
                "    return 0",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "bad.ll"))
    assert got.returncode == 3, got.stderr
    assert "is not a value of" in got.stderr
