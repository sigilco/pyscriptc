"""A mixed `and`/`or` in value position yields the joined truthiness: a bool.

Diverges from CPython, where `a or b` returns the deciding operand with its
own type. The IR has no tagged union for `T_a | T_b` (epic E3c), so
`1 or 'a'` lowers to a bool `True`, not the int `1`. Same-type `and`/`or`
still yields the operand, and mixes with a non-truthy side stay fenced.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

_PROBE = """
class Counter:
    n: int
    def __init__(self, n: int) -> None:
        self.n = n

def bump(box: Counter) -> str:
    box.n = box.n + 1
    return 'x'

def or_true() -> int:
    v: bool = 1 or 'a'
    if v:
        return 1
    return 0

def or_false_left() -> int:
    v: bool = 0 or 'a'
    if v:
        return 1
    return 0

def or_both_false() -> int:
    v: bool = 0 or ''
    if v:
        return 1
    return 0

def and_true() -> int:
    v: bool = 'a' and 1
    if v:
        return 1
    return 0

def and_false() -> int:
    v: bool = 0 and 'a'
    if v:
        return 1
    return 0

def skip() -> int:
    box: Counter = Counter(0)
    v: bool = 1 or bump(box)
    if v:
        return box.n
    return 9

def once() -> int:
    box: Counter = Counter(0)
    v: bool = 0 or bump(box)
    if v:
        return box.n
    return 9
"""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_boolop_value_matches_truthiness(tmp_path: Path) -> None:
    path = write_case(tmp_path, "boolop_value.py", _PROBE)
    expected = {
        "or_true": "1",
        "or_false_left": "1",
        "or_both_false": "0",
        "and_true": "1",
        "and_false": "0",
        "skip": "0",
        "once": "1",
    }
    for func, want in expected.items():
        got = run_cli(
            "compile",
            str(path),
            "--run",
            "--call",
            func,
            "--args",
            "[]",
            "--skip-check",
            "-o",
            str(tmp_path / func),
        )
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout.strip() == want


def test_mixed_value_with_non_truthy_side_stays_fenced(tmp_path: Path) -> None:
    path = write_case(tmp_path, "float.py", "def probe() -> int:\n    v: bool = 1 or 2.0\n    return 1\n")
    got = run_cli(
        "compile",
        str(path),
        "--skip-check",
        "--emit",
        "llvm",
        "-o",
        str(tmp_path / "float.ll"),
    )
    assert got.returncode == 3, got.stderr
    assert "and/or across two types is not lowered" in got.stderr


def test_same_type_value_still_yields_operand(tmp_path: Path) -> None:
    path = write_case(tmp_path, "same.py", "def probe() -> int:\n    v: int = 0 or 3\n    return v\n")
    got = run_cli(
        "compile",
        str(path),
        "--run",
        "--call",
        "probe",
        "--args",
        "[]",
        "--skip-check",
        "-o",
        str(tmp_path / "same"),
    )
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == "3"
