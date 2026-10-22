"""First run: `--run` without `--call`, and the up-front C toolchain check."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import ENV, ROOT, run_cli, write_case

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="clang required")

HELLO = 'def main() -> None:\n    print("hello")\n\n\nif __name__ == "__main__":\n    main()\n'
NO_GUARD_FN = "def add(a: int, b: int) -> int:\n    return a + b\n"


@needs_clang
def test_run_without_call_runs_the_main_guard(tmp_path: Path) -> None:
    path = write_case(tmp_path, "hello.py", HELLO)
    got = run_cli("compile", str(path), "--run", "--skip-check")
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == "hello"
    assert "nothing to run" not in got.stderr


@needs_clang
def test_run_without_call_and_no_entry_prints_a_hint(tmp_path: Path) -> None:
    path = write_case(tmp_path, "add.py", NO_GUARD_FN)
    got = run_cli("compile", str(path), "--run", "--skip-check")
    assert got.returncode == 0, got.stderr
    assert got.stdout == ""
    assert "nothing to run" in got.stderr
    assert "--call" in got.stderr


def _stripped_env(tmp_path: Path) -> dict[str, str]:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    return {**ENV, "PATH": str(empty), "PYSC_NO_TELEMETRY": "1"}


def test_missing_toolchain_fails_up_front_and_points_at_doctor(tmp_path: Path) -> None:
    path = write_case(tmp_path, "hello.py", HELLO)
    got = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(path), "--run", "--skip-check"],
        cwd=ROOT,
        env=_stripped_env(tmp_path),
        capture_output=True,
        text=True,
    )
    assert got.returncode != 0
    assert "no C toolchain found on PATH" in got.stderr
    assert "neither zig nor clang" in got.stderr
    assert "pip install ziglang" in got.stderr
    assert "pyscriptc doctor" in got.stderr
    assert not (tmp_path / "hello.ir.json").exists(), "the check must run before any work"


def test_missing_zig_driver_names_zig(tmp_path: Path) -> None:
    path = write_case(tmp_path, "hello.py", HELLO)
    got = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(path), "--emit", "bin", "--driver", "zig", "--skip-check"],
        cwd=ROOT,
        env=_stripped_env(tmp_path),
        capture_output=True,
        text=True,
    )
    assert got.returncode != 0
    assert "zig not found on PATH" in got.stderr


def test_emit_llvm_needs_no_toolchain(tmp_path: Path) -> None:
    path = write_case(tmp_path, "hello.py", HELLO)
    got = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(path), "--emit", "llvm", "--skip-check"],
        cwd=ROOT,
        env=_stripped_env(tmp_path),
        capture_output=True,
        text=True,
    )
    assert got.returncode == 0, got.stderr
    assert os.path.exists(got.stdout.strip())
