"""assert is if-not plus the AssertionError raise.

A true assert falls through, so the next statement runs. The message expression
runs only when the assert fails. `except AssertionError` catches it (#515).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
def boom() -> str:
    raise ValueError("side")
    return "x"

def checklist() -> int:
    assert 1 == 1
    return 1

def falls() -> int:
    n: int = 0
    assert n == 0
    n = n + 1
    return n

def truthy() -> int:
    assert 1
    return 2

def msg_skipped() -> int:
    assert True, boom()
    return 1

def msg_runs() -> int:
    try:
        assert False, boom()
        return 0
    except ValueError:
        return 2
    except Exception:
        return 1

def caught() -> int:
    try:
        assert 1 == 0
        return 0
    except Exception:
        return 4
"""


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_assert_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "assert.py", _PROBE)
    expected = {
        "checklist": "1",
        "falls": "1",
        "truthy": "2",
        "msg_skipped": "1",
        "msg_runs": "2",
        "caught": "4",
    }
    for func, want in expected.items():
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == want


def test_assert_condition_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "float.py",
            "def probe() -> int:\n    assert 1.0\n    return 1\n",
            "condition must be a bool, not a truthy value",
        ),
        (
            "message.py",
            "def probe() -> int:\n    assert False, 1\n    return 0\n",
            "an exception message is a str",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"
