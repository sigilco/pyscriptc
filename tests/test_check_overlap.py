"""Overlapped pyrefly preflight (`--emit bin`): join order and CLI behavior.

The checker runs concurrently with emit/link but its verdict still occupies the
serial check slot: it lands before the fence report, the run note and the
binary print, and a check failure masks a later emit/link error.
`PYSC_NO_CHECK_OVERLAP=1` keeps the serial call.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import ENV, ROOT, run_cli, write_case

from pyscriptc.check import CheckFailed, start_pyrefly

needs_pyrefly = pytest.mark.skipif(shutil.which("pyrefly") is None, reason="pyrefly binary not on PATH")
needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="clang required")

OK = "def probe() -> int:\n    return 1\n"
BAD = "import os\n\ndef probe() -> int:\n    v: int = os.getenv('PYSC_X')\n    return v\n"
FENCED = "from typing import Any\n\n\ndef f(x: Any) -> int:\n    return 1\n"


@needs_pyrefly
def test_join_returns_on_clean_source(tmp_path: Path) -> None:
    join = start_pyrefly(write_case(tmp_path, "ok.py", OK))
    join()
    join()


@needs_pyrefly
def test_join_reraises_check_failed(tmp_path: Path) -> None:
    join = start_pyrefly(write_case(tmp_path, "bad.py", BAD))
    with pytest.raises(CheckFailed):
        join()
    with pytest.raises(CheckFailed):
        join()


@needs_pyrefly
@needs_clang
def test_emit_bin_still_compiles(tmp_path: Path) -> None:
    path = write_case(tmp_path, "ok.py", OK)
    got = run_cli("compile", str(path), "--emit", "bin", "--call", "probe", "-o", str(tmp_path / "ok"))
    assert got.returncode == 0, got.stderr


@needs_pyrefly
@needs_clang
def test_check_failure_keeps_its_verdict(tmp_path: Path) -> None:
    path = write_case(tmp_path, "bad.py", BAD)
    got = run_cli("compile", str(path), "--emit", "bin", "--call", "probe", "-o", str(tmp_path / "bad"))
    assert got.returncode == 2, got.stderr
    assert "not assignable" in got.stderr


@needs_pyrefly
@needs_clang
def test_check_failure_still_blocks_run(tmp_path: Path) -> None:
    path = write_case(tmp_path, "bad.py", BAD)
    got = run_cli("compile", str(path), "--run", "--call", "probe", "-o", str(tmp_path / "bad"))
    assert got.returncode == 2, got.stderr
    assert "not assignable" in got.stderr
    assert got.stdout == ""


@needs_pyrefly
@needs_clang
def test_fences_report_after_pending_check(tmp_path: Path) -> None:
    path = write_case(tmp_path, "fenced.py", FENCED)
    got = run_cli("compile", str(path), "--emit", "bin", "-o", str(tmp_path / "fenced"))
    assert got.returncode == 3, got.stderr
    assert "PS1104" in got.stderr


@needs_clang
def test_late_checker_crash_reports_after_link(tmp_path: Path) -> None:
    # A pyrefly that fails on a clean module exercises the post-link join: the
    # verdict still surfaces as the compile's failure.
    path = write_case(tmp_path, "ok.py", OK)
    got = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--emit",
            "bin",
            "--call",
            "probe",
            "-o",
            str(tmp_path / "ok"),
        ],
        cwd=ROOT,
        env={**ENV, "PYREFLY_BIN": "/bin/false"},
        capture_output=True,
        text=True,
    )
    assert got.returncode == 2
    assert "pyrefly" in got.stderr


@needs_pyrefly
@needs_clang
def test_nothing_to_run_note_survives(tmp_path: Path) -> None:
    path = write_case(tmp_path, "ok.py", OK)
    got = run_cli("compile", str(path), "--run", "-o", str(tmp_path / "ok"))
    assert got.returncode == 0, got.stderr
    assert "nothing to run" in got.stderr


@needs_pyrefly
@needs_clang
def test_overlap_opt_out(tmp_path: Path) -> None:
    path = write_case(tmp_path, "ok.py", OK)
    got = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--emit",
            "bin",
            "--call",
            "probe",
            "-o",
            str(tmp_path / "ok"),
        ],
        cwd=ROOT,
        env={**ENV, "PYSC_NO_CHECK_OVERLAP": "1"},
        capture_output=True,
        text=True,
    )
    assert got.returncode == 0, got.stderr
