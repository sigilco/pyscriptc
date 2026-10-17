"""float() of an inf or nan spelling is the float const math.inf / math.nan already use.

The probe prints four digits: inf equals math.inf, -inf is below zero, inf is
above 1, and another accepted spelling equals math.inf.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
import math

def probe() -> str:
    same: int = 0
    if float("inf") == math.inf:
        same = 1
    neg: int = 0
    if float("-inf") < 0.0:
        neg = 1
    above: int = 0
    if float("inf") > 1.0:
        above = 1
    spelled: int = 0
    if float(" +Infinity ") == math.inf:
        spelled = 1
    return str(same) + str(neg) + str(above) + str(spelled)


def nan_probe() -> str:
    raw: int = 0
    if math.isnan(float("nan")):
        raw = 1
    titled: int = 0
    if math.isnan(float("NaN")):
        titled = 1
    known: int = 0
    if math.isnan(math.nan):
        known = 1
    return str(raw) + str(titled) + str(known)


def from_int() -> int:
    hit: int = 0
    if float(2) == 2.0:
        hit = 1
    return hit
"""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_float_inf_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "inf.py", _PROBE)
    for func, expected in (("probe", "1111"), ("nan_probe", "111"), ("from_int", "1")):
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == expected


def test_float_inf_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "bytes.py",
            "def probe() -> float:\n    return float(b'inf')\n",
            "float is not lowered for these arguments",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"
