"""enum: auto() joins the integer enum surface.

`auto()` and `enum.auto()` as enum body members take CPython's counter: the
first member of a class gets 1 and each explicit integer resets the counter
to that integer plus one. Each probe diffs stdout against the CPython oracle.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, run_cli, write_case


def _oracle(tmp_path: Path, name: str, source: str, func: str = "run") -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func), got.stdout


def test_auto_first(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "auto_first.py",
        "\n".join(
            [
                "from enum import IntEnum, auto",
                "",
                "class State(IntEnum):",
                "    OFF = auto()",
                "    ON = auto()",
                "",
                "def run() -> int:",
                "    print(State.OFF)",
                "    print(State.ON)",
                "    print(State.OFF + State.ON)",
                "    return 0",
                "",
            ]
        ),
    )


def test_auto_after_explicit(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "auto_after.py",
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
                "def run() -> int:",
                "    print(Level.DEBUG + Level.INFO + Level.WARN)",
                "    print(Level.ERROR + Level.CRITICAL)",
                "    return 0",
                "",
            ]
        ),
    )


def test_dotted_enum_auto(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "dotted_auto.py",
        "\n".join(
            [
                "import enum",
                "",
                "class Source(enum.IntEnum):",
                "    PROMPT = enum.auto()",
                "    COMMANDLINE = enum.auto()",
                "    ENVIRONMENT = enum.auto()",
                "",
                "def run() -> int:",
                "    print(Source.PROMPT)",
                "    print(Source.COMMANDLINE + Source.ENVIRONMENT)",
                "    return 0",
                "",
            ]
        ),
    )


def test_auto_mixed_bases(tmp_path: Path) -> None:
    """Plain Enum with integer members compares by identity the same way."""
    _oracle(
        tmp_path,
        "auto_enum_base.py",
        "\n".join(
            [
                "from enum import Enum, auto",
                "",
                "class Color(Enum):",
                "    RED = 1",
                "    GREEN = auto()",
                "",
                "def run() -> int:",
                "    same: int = 0",
                "    if Color.RED == Color.RED:",
                "        if Color.GREEN == Color.RED:",
                "            return 0",
                "        same = 1",
                "    return same",
                "",
            ]
        ),
    )


def test_auto_fences(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "auto_args.py",
        "\n".join(
            [
                "from enum import IntEnum, auto",
                "",
                "class Bad(IntEnum):",
                "    A = auto(3)",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "bad.ll"))
    assert got.returncode == 3, got.stderr
    assert "enum.auto takes no arguments" in got.stderr
    path = write_case(
        tmp_path,
        "auto_bare.py",
        "\n".join(
            [
                "from enum import auto",
                "",
                "def run() -> int:",
                "    auto()",
                "    return 0",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "bad2.ll"))
    assert got.returncode == 3, got.stderr
    assert "enum.auto is only lowered inside an enum" in got.stderr
