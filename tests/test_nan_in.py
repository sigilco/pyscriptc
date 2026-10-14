"""Float list membership is ==, so a NaN is not a member of a list of NaNs.

math.nan + 0.0 is a different NaN object from math.nan. CPython's `in` is
false for that pair, and true for the same math.nan object. The digits are
the four comparisons below.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, write_case

pytestmark = pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh

_PROBE = """
import math

def probe() -> str:
    nan: float = math.nan + 0.0
    same: int = 0
    if nan in [math.nan]:
        same = 1
    one: int = 0
    if 1.0 in [1.0]:
        one = 1
    miss: int = 0
    if nan in [1.0]:
        miss = 1
    other: int = 0
    if 1.0 in [math.nan]:
        other = 1
    return str(same) + str(one) + str(miss) + str(other)


def ints() -> int:
    hit: int = 0
    if 1 in [1, 2]:
        hit = 1
    return hit


def words() -> int:
    hit: int = 0
    if "a" in ["a", "b"]:
        hit = 1
    return hit


def eq() -> int:
    hit: int = 0
    if math.nan == math.nan:
        hit = 1
    return hit
"""


def test_nan_list_membership_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "nan_in.py", _PROBE)
    for func, expected in (("probe", "0100"), ("ints", "1"), ("words", "1"), ("eq", "0")):
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == expected
