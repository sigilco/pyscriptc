"""CLI DX: run passthrough, --print diagnostics, coverage --fail-on."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

ECHO = "import sys\n\n\ndef main() -> int:\n    return len(sys.argv)\n"

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="clang required")


@needs_clang
def test_run_forwards_args_after_separator(tmp_path: Path) -> None:
    path = write_case(tmp_path, "echo.py", ECHO)
    got = run_cli("compile", str(path), "--run", "--skip-check", "--", "a", "b")
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == "3"  # argv = [prog, a, b]


def test_separator_without_run_is_rejected(tmp_path: Path) -> None:
    path = write_case(tmp_path, "echo.py", ECHO)
    got = run_cli("compile", str(path), "--", "a")
    assert got.returncode == 2
    assert "--run" in got.stderr
