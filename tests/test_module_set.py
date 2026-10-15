"""A module set of str or int constants is the set list already stored.

Length and membership match CPython. Iteration order does not: the list keeps
insertion order, so the probe does not print the set. Duplicates keep the first.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
ARCHS = {"x86_64", "aarch64", "x86_64", "ppc64"}
NUMS = {2, 5, 2, 9}

def probe() -> int:
    present: int = 0
    if "aarch64" in ARCHS:
        present = 1
    absent: int = 0
    if "riscv64" in ARCHS:
        absent = 1
    n_present: int = 0
    if 5 in NUMS:
        n_present = 1
    n_absent: int = 0
    if 7 in NUMS:
        n_absent = 1
    return len(ARCHS) * 100000 + present * 10000 + absent * 1000 + len(NUMS) * 100 + n_present * 10 + n_absent
"""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_module_set_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "module_set.py", _PROBE)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "310310"


def test_module_set_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "added.py",
            "ARCHS = {1 + 2, 4}\n",
            "a module assignment must be a constant",
        ),
        (
            "comp.py",
            "ARCHS = {item for item in ['a', 'b']}\n",
            "SetComp is not lowered",
        ),
        (
            "float.py",
            "ARCHS = {1.5}\n",
            "set elements are int or str",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr
