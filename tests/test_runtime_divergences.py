"""Differential tests for the runtime divergence fixes (#514, #517, #518, #519, #528).

Each probe compiles with the worktree's own `pyscriptc`, runs the native
binary, and byte-compares stdout, stderr and the exit code against the
pinned CPython 3.14 interpreter running the same source.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import ROOT, write_case

ORACLE = shutil.which("python3.14") or "/usr/local/bin/python3.14"


def _oracle_bin() -> str:
    """The CPython 3.14 oracle: the pinned uv interpreter when present."""
    pinned = Path.home() / ".local/share/uv/python"
    candidates = sorted(pinned.glob("cpython-3.14.*-*/bin/python3"))
    if candidates:
        return str(candidates[-1])
    return sys.executable


ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def compile_and_run(tmp_path: Path, name: str, source: str) -> tuple[bytes, bytes, int]:
    """Compile `source`, run the binary, return (stdout, stderr, exit)."""
    path = write_case(tmp_path, name, source)
    proc = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(path), "--run", "-o", str(tmp_path / (name + ".bin"))],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise AssertionError(f"compile failed:\n{proc.stderr.decode('utf-8', 'replace')}")
    binary = tmp_path / (name + ".bin")
    run = subprocess.run([str(binary)], capture_output=True, timeout=120)
    return run.stdout, run.stderr, run.returncode


def oracle_run(tmp_path: Path, name: str, source: str) -> tuple[bytes, bytes, int]:
    """Run the same source under the pinned CPython 3.14."""
    path = write_case(tmp_path, "oracle_" + name, source)
    run = subprocess.run([_oracle_bin(), str(path)], capture_output=True, timeout=120, cwd=tmp_path)
    return run.stdout, run.stderr, run.returncode


PROBES = {
    # #514: sum over floats is CPython's Neumaier compensated sum.
    "sum_floats": """
def s(fs: list[float]) -> float:
    return sum(fs)
print(s([0.1, 0.2, 0.3]))
fs: list[float] = [0.1] * 10
print(s(fs))
print(s([1e16, 1.0, -1e16]))
print(s([1.0, 1e100, 1.0, -1e100]))
""",
    # #517: a tuple's str display quotes its str elements.
    "tuple_str_display": """
t = ("a", "b")
print(t)
u = (1, "x", 2.5, True)
print(u)
one: tuple[str, int] = ("solo", 1)
print(one)
nested: tuple[tuple[int, int], tuple[str, int]] = ((1, 2), ("a", 3))
print(nested)
""",
    # #518: a NUL inside a str does not truncate the printed value.
    "nul_in_str": """
s = "a\\x00b"
print(s)
print(len(s))
print(repr(s))
print(s + "!")
def echo(v: str) -> str:
    return v
print(repr(echo(s)))
""",
    # #519: text-mode reads apply universal newline translation.
    "universal_newlines": """
def w(p: str, s: str) -> None:
    with open(p, "w") as fh:
        fh.write(s)
def r(p: str) -> str:
    with open(p, "r") as fh:
        return fh.read()
def rb(p: str) -> bytes:
    with open(p, "rb") as fh:
        return fh.read()
def lines(p: str) -> str:
    acc: str = ""
    with open(p, "r") as fh:
        for ln in fh:
            acc = acc + repr(ln)
    return acc
w("nlprobe.txt", "a\\r\\nb\\rc\\n")
print(repr(r("nlprobe.txt")))
print(repr(rb("nlprobe.txt")))
print(lines("nlprobe.txt"))
""",
    # #528: round(x, n) matches CPython's exact-decimal rounding, and out
    # of range ndigits fences at compile time (asserted separately below).
    "round_nd": """
print(round(2.675, 2))
print(round(16.05, 1))
print(round(0.5, 1))
print(round(1.005, 2))
print(round(123.456, 0))
print(round(2.5, 0))
x: float = 1.23456789
print(round(x, 4))
print(round(-2.675, 2))
nines: float = 9.99
r9: float = round(nines, 1)
print(r9 == 10.0)
""",
}

FENCES = {
    "round_nd_negative": "x: float = 1234.0\nprint(round(x, -2))\n",
    "round_nd_large": "print(round(1.5, 9))\n",
}


@pytest.mark.parametrize("name", sorted(PROBES))
def test_matches_cpython(tmp_path: Path, name: str) -> None:
    got = compile_and_run(tmp_path, name + ".py", PROBES[name])
    want = oracle_run(tmp_path, name + ".py", PROBES[name])
    assert got == want


def test_round_carry_is_exact(tmp_path: Path) -> None:
    """The 9.99 -> 10.0 carry round-trips to CPython's exact float. The
    spelling of that float rides the separate float-print seam, so the
    assertion is the numeric identity, not the text."""
    source = """
nines: float = 9.99
r: float = round(nines, 1)
print(r == 10.0)
up: float = 0.19999999999999998
print(round(up, 1))
"""
    got = compile_and_run(tmp_path, "carry.py", source)
    want = oracle_run(tmp_path, "carry.py", source)
    assert got == want


@pytest.mark.parametrize("name", sorted(FENCES))
def test_round_nd_fences(tmp_path: Path, name: str) -> None:
    """Out-of-range constant ndigits is a compile-time PS2003 fence, not a
    runtime ValueError."""
    path = write_case(tmp_path, name + ".py", FENCES[name])
    proc = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(path), "-o", str(tmp_path / name)],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
    )
    assert proc.returncode != 0
    assert b"PS2003" in proc.stderr
    assert b"between 0 and 8" in proc.stderr
