"""CLI DX: run passthrough, --print diagnostics, coverage --fail-on."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
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


def test_compile_single_fence_output_unchanged(tmp_path: Path) -> None:
    path = write_case(tmp_path, "single.py", "def f(x: int | str) -> int:\n    return 1\n")
    got = run_cli("compile", str(path), "--skip-check")
    assert got.returncode == 3
    assert "PS1101" in got.stderr
    assert "more fence" not in got.stderr


def test_compile_root_fence_and_cascade_count(tmp_path: Path) -> None:
    source = "def f() -> int:\n    x = 1 + 'a'\n    y = x + 2\n    return y\n"
    path = write_case(tmp_path, "cascade.py", source)
    got = run_cli("compile", str(path), "--skip-check")
    assert got.returncode == 3
    assert "PS1001" in got.stderr
    assert "(and 2 more fences that follow from it)" in got.stderr
    assert "y is not a local or parameter" not in got.stderr


def test_compile_corpus_prints_at_most_three_fences() -> None:
    source_path = Path("benchmarks/real_scripts/scripts/duplicate_file_finder.py")
    if not source_path.exists():
        pytest.skip("corpus file missing")
    got = run_cli("compile", str(source_path), "--skip-check")
    assert got.returncode == 3
    assert got.stderr.count("PS2003:") == 1
    assert "(and 20 more fences that follow from it)" in got.stderr
    frame_count = len([line for line in got.stderr.splitlines() if re.match(r"^.+:\d+:\d+: PS\d+:", line)])
    assert 1 <= frame_count <= 3


def test_coverage_json_marks_root_cause(tmp_path: Path) -> None:
    source = "def f() -> int:\n    x = 1 + 'a'\n    return x\n"
    path = write_case(tmp_path, "root_cause.py", source)
    got = run_cli("coverage", str(path), "--json")
    assert got.returncode == 0, got.stderr
    report = json.loads(got.stdout)
    defs = report["definitions"]
    assert len(defs) == 1
    d = defs[0]
    assert "rootCause" in d
    assert d["rootCause"] == d["fences"][0]
    assert d["name"] == "f"


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


def _run_env(env_extra: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    import os
    import subprocess
    import sys

    env = {**os.environ, **env_extra}
    return subprocess.run([sys.executable, "-m", "pyscriptc", *args], capture_output=True, text=True, env=env, cwd=ROOT)


def test_env_target_without_zig_driver_is_rejected(tmp_path: Path) -> None:
    path = write_case(tmp_path, "ok.py", CLEAN)
    got = _run_env(
        {"PYSC_TARGET": "aarch64-linux-gnu", "PYSC_CC": "clang"},
        "compile",
        str(path),
        "--emit",
        "obj",
        "--skip-check",
    )
    assert got.returncode != 0
    assert "--target requires --driver zig" in got.stderr


def test_env_cc_invalid_value(tmp_path: Path) -> None:
    path = write_case(tmp_path, "ok.py", CLEAN)
    got = _run_env({"PYSC_CC": "gcc"}, "compile", str(path), "--skip-check")
    assert got.returncode == 1
    assert "PYSC_CC must be" in got.stderr


@needs_clang
def test_flag_wins_over_env(tmp_path: Path) -> None:
    path = write_case(tmp_path, "ok.py", CLEAN)
    got = _run_env({"PYSC_CC": "gcc"}, "compile", str(path), "--driver", "clang", "--emit", "obj", "--skip-check")
    assert got.returncode == 0, got.stderr


@needs_clang
@pytest.mark.parametrize(("emit", "suffix"), [("asm", ".s"), ("obj", ".o")])
def test_emit_asm_obj_beside_source(tmp_path: Path, emit: str, suffix: str) -> None:
    path = write_case(tmp_path, "ok.py", CLEAN)
    got = run_cli("compile", str(path), "--emit", emit, "--skip-check")
    assert got.returncode == 0, got.stderr
    assert path.with_suffix(suffix).is_file()


@needs_clang
def test_out_dir_collects_artifacts(tmp_path: Path) -> None:
    path = write_case(tmp_path, "ok.py", CLEAN)
    out = tmp_path / "build"
    got = run_cli("compile", str(path), "--emit", "obj", "--out-dir", str(out), "--skip-check")
    assert got.returncode == 0, got.stderr
    assert (out / "ok.o").is_file()
    assert (out / "ok.ll").is_file()
    assert not path.with_suffix(".o").exists()
