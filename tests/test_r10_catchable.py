"""R10: runtime traps raise through PsExc instead of trapping at the IR level.

`//` and `%` by zero raise ZeroDivisionError, checked int overflow raises
OverflowError, a negative shift raises ValueError, and float traps raise
ZeroDivisionError — all catchable by `except`. The widened builtin table
lowers TypeError, AttributeError and FileNotFoundError as raise/except names.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, write_case


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_int_traps_are_catchable(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "traps.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    caught: int = 0",
                "    try:",
                "        x: int = 1 // 0",
                "    except ZeroDivisionError:",
                "        caught = caught + 1",
                "    try:",
                "        y: int = 7 % 0",
                "    except ZeroDivisionError:",
                "        caught = caught + 2",
                "    try:",
                "        s: int = 1 << -1",
                "    except ValueError:",
                "        caught = caught + 4",
                "    try:",
                "        r: int = 8 >> -2",
                "    except ValueError:",
                "        caught = caught + 8",
                "    return caught",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "15\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_float_traps_are_catchable(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "ftraps.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    caught: int = 0",
                "    try:",
                "        q: float = 1.0 / 0.0",
                "    except ZeroDivisionError:",
                "        caught = caught + 1",
                "    try:",
                "        m: float = 1.0 % 0.0",
                "    except ZeroDivisionError:",
                "        caught = caught + 2",
                "    try:",
                "        d: float = 1.0 // 0.0",
                "    except ZeroDivisionError:",
                "        caught = caught + 4",
                "    try:",
                "        p: float = 0.0 ** -1.0",
                "    except ZeroDivisionError:",
                "        caught = caught + 8",
                "    return caught",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "15\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_widened_exception_names(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "names.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    caught: int = 0",
                "    try:",
                '        raise TypeError("t")',
                "    except TypeError:",
                "        caught = caught + 1",
                "    try:",
                '        raise AttributeError("a")',
                "    except AttributeError:",
                "        caught = caught + 2",
                "    try:",
                '        raise FileNotFoundError("f")',
                "    except FileNotFoundError:",
                "        caught = caught + 4",
                "    try:",
                '        raise TypeError("u")',
                "    except Exception:",
                "        caught = caught + 8",
                "    return caught",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "15\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_uncaught_trap_reports_name(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "uncaught.py",
        "\n".join(
            [
                "def boom() -> int:",
                "    x: int = 1 // 0",
                "    return x",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "boom")
    assert got.returncode == 1
    assert "ZeroDivisionError" in got.stderr
