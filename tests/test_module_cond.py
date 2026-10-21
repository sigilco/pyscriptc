"""Module-scope `a = x if c else y` and `a = x or y` fold over constants.

Module scope has no body to hold the `if`/boolop prelude temps, so a
conditional or boolean operation folds when every operand is already a
constant. An operand that does not fold fences with PS2002 — never a
SpikeError. A non-constant value inside a script lowers in the entry like
any other statement.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
X: int = 1
Y: int = 2
ZERO: int = 0
FLAG: bool = True

A: int = X if FLAG else Y
B: int = X or Y
C = X if not FLAG else Y
D = ZERO or Y
E = X and Y

def probe() -> int:
    return A * 10000 + B * 1000 + C * 100 + D * 10 + E
"""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_module_conditional_folds_match_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "module_cond.py", _PROBE)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "11222"


@pytest.mark.linux_container
def test_module_annotated_ifexp_lowers_in_script_entry(tmp_path: Path) -> None:
    source = "X: int = 4\na: int = len('ab') if X else 0\nprint(a)\n"
    path = write_case(tmp_path, "module_cond_script.py", source)
    got = run_cli("compile", str(path), "--run", "-o", str(tmp_path / "module_cond_script"))
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == "2"


def test_module_conditional_fence_is_clean(tmp_path: Path) -> None:
    cases = [
        (
            "nested_boolop.py",
            "X: int = 1\nY: int = 2\na: list[int] = [X or Y]\n",
        ),
    ]
    for name, source in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        # Exit 3 is a fence; a SpikeError would die with exit 1 and a traceback.
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert "a module assignment must be a constant" in got.stderr


def test_module_conditional_len_of_const_folds(tmp_path: Path) -> None:
    # T2-5: len() of a const folds at lowering, so the annotated module
    # assignment is a constant again and the conditional/boolop un-fences.
    cases = [
        (
            "test_call.py",
            "X: int = 1\nY: int = 2\nNAME: str = 'ab'\na: int = X if len(NAME) else Y\n"
            "def probe() -> int:\n    return a\n",
        ),
        (
            "boolop_call.py",
            "X: int = 1\nNAME: str = 'ab'\na: int = len(NAME) or X\ndef probe() -> int:\n    return a\n",
        ),
    ]
    for name, source in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 0, f"{name}\n{got.stderr}"
