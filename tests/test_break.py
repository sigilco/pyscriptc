"""break exits the nearest loop. Code after the loop still runs.

The checklist leaves i at 0. A nested break leaves the outer loop running.
A rejected path after the break does not run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
def checklist() -> int:
    i: int = 0
    while i < 3:
        break
    return i

def after() -> int:
    i: int = 0
    hit: int = 0
    while i < 5:
        i = i + 1
        if i == 2:
            break
        hit = hit + 1
    return i * 10 + hit

def nested() -> int:
    total: int = 0
    i: int = 0
    while i < 2:
        j: int = 0
        while j < 5:
            if j == 1:
                break
            j = j + 1
        total = total + j
        i = i + 1
    return total

def for_break() -> int:
    total: int = 0
    for i in range(5):
        if i == 2:
            break
        total = total + i
    return total

def nested_for() -> int:
    total: int = 0
    outer: int = 0
    for i in range(3):
        for j in range(5):
            if j == 1:
                break
        outer = outer + 1
        total = total + outer
    return total
"""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_break_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "brk.py", _PROBE)
    expected = {
        "checklist": "0",
        "after": "21",
        "nested": "2",
        "for_break": "1",
        "nested_for": "6",
    }
    for func, want in expected.items():
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == want


def test_break_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "outside.py",
            "def probe() -> int:\n    break\n    return 0\n",
            "break outside a loop is not lowered",
        ),
        (
            "cont.py",
            "def probe() -> int:\n    continue\n    return 0\n",
            "continue outside a loop is not lowered",
        ),
        (
            "while_else.py",
            (
                "def probe() -> int:\n"
                "    i: int = 0\n"
                "    while i < 1:\n"
                "        break\n"
                "    else:\n"
                "        i = 1\n"
                "    return i\n"
            ),
            "while-else is not lowered",
        ),
        (
            "for_else.py",
            ("def probe() -> int:\n    for i in range(2):\n        break\n    else:\n        return 0\n    return 1\n"),
            "for-else is not lowered",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"
