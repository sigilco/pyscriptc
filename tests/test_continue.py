"""continue skips the rest of the nearest loop body and tests the loop again.

The checklist adds one, continues, and returns that 1. A nested continue does
not skip the outer loop. The for-loop step still runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
def checklist() -> int:
    i: int = 0
    while i < 1:
        i = i + 1
        continue
    return i

def skipped() -> int:
    i: int = 0
    total: int = 0
    while i < 3:
        i = i + 1
        if i == 2:
            continue
        total = total + i
    return total

def nested() -> int:
    total: int = 0
    i: int = 0
    while i < 2:
        j: int = 0
        while j < 3:
            j = j + 1
            if j == 2:
                continue
            total = total + 1
        i = i + 1
    return total

def for_cont() -> int:
    total: int = 0
    for i in range(4):
        if i == 1:
            continue
        total = total + i
    return total

def both() -> int:
    i: int = 0
    total: int = 0
    while i < 5:
        i = i + 1
        if i == 2:
            continue
        if i == 4:
            break
        total = total + i
    return total
"""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_continue_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "cont.py", _PROBE)
    expected = {
        "checklist": "1",
        "skipped": "4",
        "nested": "4",
        "for_cont": "5",
        "both": "4",
    }
    for func, want in expected.items():
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == want


def test_continue_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "outside.py",
            "def probe() -> int:\n    continue\n    return 0\n",
            "continue outside a loop is not lowered",
        ),
        (
            "while_else.py",
            (
                "def probe() -> int:\n"
                "    i: int = 0\n"
                "    while i < 1:\n"
                "        i = i + 1\n"
                "        continue\n"
                "    else:\n"
                "        i = 2\n"
                "    return i\n"
            ),
            "while-else is not lowered",
        ),
        (
            "for_else.py",
            (
                "def probe() -> int:\n"
                "    for i in range(2):\n"
                "        continue\n"
                "    else:\n"
                "        return 0\n"
                "    return 1\n"
            ),
            "for-else is not lowered",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"
