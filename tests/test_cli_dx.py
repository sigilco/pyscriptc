"""CLI DX: run passthrough, --print diagnostics, coverage --fail-on."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from tests.test_gates import ROOT, run_cli, write_case

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


FENCED = "from typing import Any\n\n\ndef f(x: Any) -> int:\n    return 1\n"
CLEAN = "def f(x: int) -> int:\n    return x\n"


def test_print_diagnostics_matches_coverage_json(tmp_path: Path) -> None:
    path = write_case(tmp_path, "mix.py", FENCED)
    got = run_cli("compile", str(path), "--print", "diagnostics")
    assert got.returncode == 3, got.stderr
    cov = run_cli("coverage", str(path), "--json")
    assert json.loads(got.stdout) == json.loads(cov.stdout)
    assert json.loads(got.stdout)["fences"][0]["code"] == "PS1104"
    assert not path.with_suffix(".ir.json").exists()


def test_print_diagnostics_clean_exits_zero(tmp_path: Path) -> None:
    path = write_case(tmp_path, "ok.py", CLEAN)
    got = run_cli("compile", str(path), "--print", "diagnostics")
    assert got.returncode == 0, got.stderr
    assert json.loads(got.stdout)["fences"] == []


@pytest.mark.parametrize(
    ("source", "mode", "code"),
    [
        (FENCED, None, 0),
        (FENCED, "any", 1),
        (FENCED, "blockers", 1),
        (CLEAN, "any", 0),
        (CLEAN, "blockers", 0),
    ],
)
def test_coverage_fail_on(tmp_path: Path, source: str, mode: str | None, code: int) -> None:
    path = write_case(tmp_path, "case.py", source)
    extra = ["--fail-on", mode] if mode else []
    assert run_cli("coverage", str(path), *extra).returncode == code


def test_doctor_json_reports_toolchain() -> None:
    got = run_cli("doctor", "--json")
    assert got.returncode == 0, got.stderr
    info = json.loads(got.stdout)
    assert info["checker"]["source"] in {"env", "interpreter", "user-scheme", "path", "-m-fallback"}
    assert info["install"] in {"wheel", "tool", "binary", "source"}
    assert {"clang", "zig", "cache_dir", "default_target"} <= info.keys()


def test_doctor_text_and_env_source(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    env = {**os.environ, "PYREFLY_BIN": "/bin/echo"}
    got = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "doctor"], capture_output=True, text=True, env=env, cwd=ROOT
    )
    assert "source: env" in got.stdout, got.stdout + got.stderr
