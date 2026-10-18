"""Pyright preflight: `--checker pyright` runs alongside the mypy gate.

`PYRIGHT_BIN` picks the binary (default `pyright` on PATH). Parity comes
from the vendored `src/pyscriptc/_pyright_stubs/` (`struct.unpack*`,
`functools.partial` yield `Any` like the mypy plugin hooks). The known
divergence is locked by the last test: `frozenset(...)` and `range(...)`
at `set`/`list` annotation sites are pyright errors that mypy accepts,
because `builtins` cannot be partially stubbed.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

from pyscriptc.check import CheckFailed, run_checker

pyright_bin = os.environ.get("PYRIGHT_BIN", "pyright")
pytestmark = pytest.mark.skipif(
    shutil.which(pyright_bin) is None,
    reason="pyright binary not found (set PYRIGHT_BIN or install pyright)",
)


def test_clean_program_passes_both_checkers(tmp_path: Path) -> None:
    source = """
def probe() -> int:
    xs: list[int] = [1, 2, 3]
    return xs[0] + len(xs)
"""
    path = write_case(tmp_path, "clean.py", source)
    run_checker(path, "mypy")
    run_checker(path, "pyright")


def test_type_error_fails_both_checkers(tmp_path: Path) -> None:
    source = """
import os

def probe() -> int:
    v: int = os.getenv("PYSC_TEST_ENV")
    return v
"""
    path = write_case(tmp_path, "mismatch.py", source)
    with pytest.raises(CheckFailed):
        run_checker(path, "mypy")
    with pytest.raises(CheckFailed) as excinfo:
        run_checker(path, "pyright")
    # mypy-style normalization: `path:line: error: <first line> [rule]`,
    # one-based lines out of pyright's zero-based JSON ranges.
    assert re.search(rf"{re.escape(str(path.resolve()))}:\d+: error: ", excinfo.value.output)
    assert "[reportAssignmentType]" in excinfo.value.output


def test_struct_unpack_and_functools_partial_parity(tmp_path: Path) -> None:
    source = """
import functools
import struct

def f(a: int, b: int) -> int:
    return a + b

def probe() -> int:
    data: bytes = struct.pack(">ii", 3, 4)
    vals: list[int] = struct.unpack(">ii", data)
    p = functools.partial(f, 1)
    return vals[0] + p(2)
"""
    path = write_case(tmp_path, "realign.py", source)
    run_checker(path, "mypy")
    run_checker(path, "pyright")


def test_frozenset_set_is_the_documented_divergence(tmp_path: Path) -> None:
    source = """
def probe() -> int:
    xs: set[int] = frozenset([1, 2, 3])
    return len(xs)
"""
    path = write_case(tmp_path, "frozenset_set.py", source)
    run_checker(path, "mypy")  # plugin hook accepts the realignment
    with pytest.raises(CheckFailed) as excinfo:
        run_checker(path, "pyright")  # builtins cannot be partially stubbed
    assert "[reportAssignmentType]" in excinfo.value.output


def test_pyright_bin_missing_reports_not_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYRIGHT_BIN", "pyright-does-not-exist-xyz")
    path = write_case(tmp_path, "any.py", "def probe() -> int:\n    return 1\n")
    with pytest.raises(CheckFailed, match="pyright is not installed"):
        run_checker(path, "pyright")


def test_cli_checker_flag_routes_to_pyright(tmp_path: Path) -> None:
    source = "def probe() -> int:\n    return 1\n"
    path = write_case(tmp_path, "cli_ok.py", source)
    got = run_cli("compile", str(path), "--checker", "pyright", "--emit", "llvm", "-o", str(tmp_path / "ok.ll"))
    assert got.returncode == 0, got.stderr
