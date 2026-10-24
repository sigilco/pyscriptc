"""Differential property-based testing pilot using Hypothesis against CPython 3.14.

Tests compiled native binary behavior against CPython 3.14 oracle for core
integer arithmetic, bitwise logic, and string operations. Uses bounded
example counts and deterministic pseudo-random seeds (derandomize=True)
to ensure test execution is reproducible in CI and fast on local developer
machines.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

ROOT = Path(__file__).resolve().parents[1]

INT_HARNESS = """import sys

def main() -> None:
    op: str = sys.argv[1]
    a: int = int(sys.argv[2])
    b: int = int(sys.argv[3])
    if op == "add":
        print(a + b)
    elif op == "sub":
        print(a - b)
    elif op == "mul":
        print(a * b)
    elif op == "floordiv":
        print(a // b)
    elif op == "mod":
        print(a % b)
    elif op == "and":
        print(a & b)
    elif op == "or":
        print(a | b)
    elif op == "xor":
        print(a ^ b)

if __name__ == "__main__":
    main()
"""

STR_HARNESS = """import sys

def main() -> None:
    op: str = sys.argv[1]
    s1: str = sys.argv[2]
    s2: str = sys.argv[3]
    if op == "concat":
        print(s1 + s2)
    elif op == "len":
        print(len(s1))
    elif op == "find":
        print(s1.find(s2))
    elif op == "startswith":
        print(s1.startswith(s2))
    elif op == "endswith":
        print(s1.endswith(s2))

if __name__ == "__main__":
    main()
"""


@pytest.fixture(scope="module")
def int_harness(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    tmp_dir = tmp_path_factory.mktemp("hypothesis_int")
    src = tmp_dir / "int_ops.py"
    src.write_text(INT_HARNESS, encoding="utf-8")
    bin_path = tmp_dir / "int_ops.bin"
    compiled = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(src),
            "--emit",
            "bin",
            "-o",
            str(bin_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert compiled.returncode == 0, compiled.stderr + compiled.stdout
    return src, bin_path


@pytest.fixture(scope="module")
def str_harness(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    tmp_dir = tmp_path_factory.mktemp("hypothesis_str")
    src = tmp_dir / "str_ops.py"
    src.write_text(STR_HARNESS, encoding="utf-8")
    bin_path = tmp_dir / "str_ops.bin"
    compiled = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(src),
            "--emit",
            "bin",
            "-o",
            str(bin_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert compiled.returncode == 0, compiled.stderr + compiled.stdout
    return src, bin_path


@given(
    a=st.integers(min_value=-100_000, max_value=100_000),
    b=st.integers(min_value=-100_000, max_value=100_000),
)
@settings(max_examples=25, derandomize=True, deadline=None)
def test_integer_arithmetic(int_harness: tuple[Path, Path], a: int, b: int) -> None:
    src, bin_path = int_harness
    for op in ("add", "sub", "mul"):
        native = subprocess.run(
            [str(bin_path), op, str(a), str(b)],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        oracle = subprocess.run(
            [sys.executable, str(src), op, str(a), str(b)],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        assert native.stdout == oracle.stdout, (
            f"mismatch for {op}({a}, {b}): native={native.stdout!r}, oracle={oracle.stdout!r}"
        )


@given(
    a=st.integers(min_value=-100_000, max_value=100_000),
    b=st.integers(min_value=-100_000, max_value=100_000).filter(lambda x: x != 0),
)
@settings(max_examples=25, derandomize=True, deadline=None)
def test_integer_division(int_harness: tuple[Path, Path], a: int, b: int) -> None:
    src, bin_path = int_harness
    for op in ("floordiv", "mod"):
        native = subprocess.run(
            [str(bin_path), op, str(a), str(b)],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        oracle = subprocess.run(
            [sys.executable, str(src), op, str(a), str(b)],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        assert native.stdout == oracle.stdout, (
            f"mismatch for {op}({a}, {b}): native={native.stdout!r}, oracle={oracle.stdout!r}"
        )


@given(
    a=st.integers(min_value=-10_000, max_value=10_000),
    b=st.integers(min_value=-10_000, max_value=10_000),
)
@settings(max_examples=25, derandomize=True, deadline=None)
def test_integer_bitwise(int_harness: tuple[Path, Path], a: int, b: int) -> None:
    src, bin_path = int_harness
    for op in ("and", "or", "xor"):
        native = subprocess.run(
            [str(bin_path), op, str(a), str(b)],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        oracle = subprocess.run(
            [sys.executable, str(src), op, str(a), str(b)],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        assert native.stdout == oracle.stdout, (
            f"mismatch for {op}({a}, {b}): native={native.stdout!r}, oracle={oracle.stdout!r}"
        )


@given(
    s1=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\0\r\n"),
        min_size=0,
        max_size=30,
    ),
    s2=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\0\r\n"),
        min_size=0,
        max_size=10,
    ),
)
@settings(max_examples=25, derandomize=True, deadline=None)
def test_string_concatenation_and_len(str_harness: tuple[Path, Path], s1: str, s2: str) -> None:
    src, bin_path = str_harness
    for op in ("concat", "len"):
        native = subprocess.run(
            [str(bin_path), op, s1, s2],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        oracle = subprocess.run(
            [sys.executable, str(src), op, s1, s2],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        assert native.stdout == oracle.stdout, (
            f"mismatch for {op}({s1!r}, {s2!r}): native={native.stdout!r}, oracle={oracle.stdout!r}"
        )


@given(
    s1=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\0\r\n"),
        min_size=0,
        max_size=30,
    ),
    s2=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\0\r\n"),
        min_size=0,
        max_size=10,
    ),
)
@settings(max_examples=25, derandomize=True, deadline=None)
def test_string_search(str_harness: tuple[Path, Path], s1: str, s2: str) -> None:
    src, bin_path = str_harness
    for op in ("find", "startswith", "endswith"):
        native = subprocess.run(
            [str(bin_path), op, s1, s2],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        oracle = subprocess.run(
            [sys.executable, str(src), op, s1, s2],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        assert native.stdout == oracle.stdout, (
            f"mismatch for {op}({s1!r}, {s2!r}): native={native.stdout!r}, oracle={oracle.stdout!r}"
        )
