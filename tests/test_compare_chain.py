"""A chained comparison is compares joined by and. The middle runs once.

A true chain `0 < middle < 10` increments the counter once. A false chain
`0 < low < right` increments the middle once and does not call the right
operand, because the first link is false. Digits are the checklist, those
counters, a false chain whose right does run, a mixed-operator chain, and a
longer chain.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
def middle(hits: list[int]) -> int:
    hits[0] = hits[0] + 1
    return hits[0]

def low(hits: list[int]) -> int:
    hits[0] = hits[0] + 1
    return 0

def right(hits: list[int]) -> int:
    hits[0] = hits[0] + 1
    return 0

def checklist() -> int:
    if 1 < 2 < 3:
        return 1
    return 0

def probe() -> str:
    true_mid: list[int] = [0]
    true_hit: int = 0
    if 0 < middle(true_mid) < 10:
        true_hit = 1
    false_mid: list[int] = [0]
    false_right: list[int] = [0]
    false_hit: int = 0
    if 0 < low(false_mid) < right(false_right):
        false_hit = 1
    both_mid: list[int] = [0]
    both_right: list[int] = [0]
    both_hit: int = 0
    if 0 < middle(both_mid) < right(both_right):
        both_hit = 1
    mixed: int = 0
    if 1 < 3 > 2:
        mixed = 1
    long_mid: list[int] = [0]
    long_late: list[int] = [0]
    long_hit: int = 0
    if 0 < middle(long_mid) < 10 < right(long_late):
        long_hit = 1
    return (
        str(checklist())
        + str(true_mid[0])
        + str(true_hit)
        + str(false_mid[0])
        + str(false_right[0])
        + str(false_hit)
        + str(both_mid[0])
        + str(both_right[0])
        + str(both_hit)
        + str(mixed)
        + str(long_mid[0])
        + str(long_late[0])
        + str(long_hit)
    )

def floats() -> int:
    hit: int = 0
    if 0.0 < 1.5 < 2.0:
        hit = 1
    return hit

def words() -> int:
    hit: int = 0
    if "a" < "b" < "c":
        hit = 1
    return hit

def membership() -> int:
    hit: int = 0
    if 0 < 1 in (0, 1):
        hit = 1
    return hit

def stored() -> int:
    ok: bool = 1 == 1 == 1
    if ok:
        return 1
    return 0
"""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_compare_chain_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "chain.py", _PROBE)
    expected = {
        "probe": "1111001101110",
        "floats": "1",
        "words": "1",
        "membership": "1",
        "stored": "1",
    }
    for func, want in expected.items():
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == want


def test_compare_chain_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "is_chain.py",
            "def probe() -> int:\n    item: int = 1\n    if item is None is None:\n        return 1\n    return 0\n",
            "is is not lowered",
        ),
        (
            "is_value.py",
            (
                "def probe() -> int:\n"
                "    item: int = 1\n"
                "    other: int = 1\n"
                "    if item is other:\n"
                "        return 1\n"
                "    return 0\n"
            ),
            "is is not lowered",
        ),
        (
            "mixed.py",
            "def probe() -> int:\n    if 1 < 2 < 'a':\n        return 1\n    return 0\n",
            "comparisons need two values of the same scalar type",
        ),
        (
            "bool_order.py",
            "def probe() -> int:\n    if True < False < True:\n        return 1\n    return 0\n",
            "bool ordering is not lowered",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"
