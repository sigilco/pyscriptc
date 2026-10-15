"""`2 ** 3` is a loop of `*`. A negative exponent stays fenced.

The base and the exponent are ints and each runs once. `0 ** 0` is 1.
A float operand and `**` on a non-int stay fenced.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
class Counter:
    n: int
    def __init__(self, n: int) -> None:
        self.n = n

def bump(box: Counter) -> int:
    box.n = box.n + 1
    return 3

def checklist() -> int:
    return 2 ** 3

def zero() -> int:
    return 0 ** 0

def zero_pos() -> int:
    return 0 ** 5

def to_zero() -> int:
    return 5 ** 0

def neg_base() -> int:
    return (-2) ** 3

def neg_even() -> int:
    return (-2) ** 2

def right() -> int:
    return 2 ** 3 ** 2

def grouped() -> int:
    return (2 ** 3) ** 2

def once() -> int:
    box: Counter = Counter(0)
    value: int = 2 ** bump(box)
    return value * 10 + box.n

def base_once() -> int:
    box: Counter = Counter(0)
    value: int = bump(box) ** 2
    return value * 10 + box.n
"""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_pow_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "pow.py", _PROBE)
    expected = {
        "checklist": "8",
        "zero": "1",
        "zero_pos": "0",
        "to_zero": "1",
        "neg_base": "-8",
        "neg_even": "4",
        "right": "512",
        "grouped": "64",
        "once": "81",
        "base_once": "91",
    }
    for func, want in expected.items():
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == want


def test_pow_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "neg.py",
            "def probe() -> int:\n    return 2 ** -1\n",
            "a negative exponent is not lowered",
        ),
        (
            "float_base.py",
            "def probe() -> float:\n    return 2.0 ** 3\n",
            "** is only lowered for int",
        ),
        (
            "float_exp.py",
            "def probe() -> float:\n    return 2 ** 3.0\n",
            "** is only lowered for int",
        ),
        (
            "text.py",
            'def probe() -> int:\n    return "ab" ** 2\n',
            "** is only lowered for int",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        args = ["compile", str(path), "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll")]
        if name in {"text.py", "neg.py"}:
            args = ["compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll")]
        got = run_cli(*args)
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_computed_negative_does_not_return_a_float(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "dyn.py",
        "\n".join(
            [
                "def probe() -> int:",
                "    e: int = 0 - 1",
                "    try:",
                "        n: int = 2 ** e",
                "        return n",
                "    except ValueError:",
                "        return 7",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == "7"
