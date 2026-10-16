"""A mixed `or` in a condition is truthiness joined by bool `or`.

`1 or 'a'` is true, and the right side runs only when the left is false.
Used as a value the same join applies and yields a bool (test_e1_boolop_value).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, write_case

_PROBE = """
class Counter:
    n: int
    def __init__(self, n: int) -> None:
        self.n = n

def bump(box: Counter) -> int:
    box.n = box.n + 1
    return box.n

def checklist() -> int:
    if 1 or 'a':
        return 1
    return 0

def false_left() -> int:
    if 0 or 'a':
        return 1
    return 0

def both_false() -> int:
    if 0 or '':
        return 1
    return 0

def and_true() -> int:
    if 'a' and 1:
        return 1
    return 0

def and_false() -> int:
    if 0 and 'a':
        return 1
    return 0

def skip() -> int:
    box: Counter = Counter(0)
    if 1 or bump(box):
        return box.n
    return 9

def once() -> int:
    box: Counter = Counter(0)
    if 0 or bump(box):
        return box.n
    return 9

def filtered() -> int:
    xs: list[int] = [i for i in range(3) if i or 'a']
    return len(xs)

def asserted() -> int:
    assert 1 or 'a'
    return 1

def spinning() -> int:
    n: int = 0
    while 0 or n < 1:
        n = n + 1
    return n

def same() -> int:
    if 1 or 0:
        return 1
    return 0
"""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_or_mixed_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "or_mixed.py", _PROBE)
    expected = {
        "checklist": "1",
        "false_left": "1",
        "both_false": "0",
        "and_true": "1",
        "and_false": "0",
        "skip": "0",
        "once": "1",
        "filtered": "3",
        "asserted": "1",
        "spinning": "1",
        "same": "1",
    }
    for func, want in expected.items():
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == want
