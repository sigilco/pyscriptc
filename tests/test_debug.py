"""`compile --debug` / `--debug-dir` / `PYSC_DEBUG` coverage."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args],
        cwd=ROOT,
        env=ENV if env is None else env,
        capture_output=True,
        text=True,
    )


def test_debug_prints_stage_table(tmp_path: Path) -> None:
    out = tmp_path / "main.ll"
    proc = run_cli("compile", str(EXAMPLES / "sum_squares.py"), "--emit", "llvm", "-o", str(out), "--debug")
    assert proc.returncode == 0, proc.stderr
    assert "[debug] stage" in proc.stderr
    assert "[debug] lower" in proc.stderr
    assert "[debug] emit_llvm" in proc.stderr
    assert "[debug] total" in proc.stderr


def test_debug_dir_writes_artifacts(tmp_path: Path) -> None:
    out = tmp_path / "main.ll"
    dbg = tmp_path / "dbg"
    proc = run_cli(
        "compile",
        str(EXAMPLES / "sum_squares.py"),
        "--emit",
        "llvm",
        "-o",
        str(out),
        "--debug-dir",
        str(dbg),
    )
    assert proc.returncode == 0, proc.stderr
    assert (dbg / "ir.json").read_text() == Path(str(out) + ".ir.json").read_text()
    assert (dbg / "out.ll").read_text() == out.read_text()
    summary = json.loads((dbg / "summary.json").read_text())
    assert summary["status"] == "ok"
    assert [stage["name"] for stage in summary["stages"]] == ["lower", "check", "validate", "emit_llvm"]
    assert summary["fences"] == []


def test_debug_does_not_flip_ir(tmp_path: Path) -> None:
    # Same output path and a pinned hash seed: emission order is set-ordered,
    # so the seed has to match for a byte-identical A/B.
    env = {**ENV, "PYTHONHASHSEED": "0"}
    out = tmp_path / "main.ll"
    src = EXAMPLES / "sum_squares.py"
    proc_plain = run_cli("compile", str(src), "--emit", "llvm", "-o", str(out), env=env)
    ll_plain = out.read_text()
    ir_plain = Path(str(out) + ".ir.json").read_text()
    proc_debug = run_cli(
        "compile",
        str(src),
        "--emit",
        "llvm",
        "-o",
        str(out),
        "--debug=all",
        "--debug-dir",
        str(tmp_path / "d"),
        env=env,
    )
    assert proc_plain.returncode == proc_debug.returncode == 0
    assert ll_plain == out.read_text()
    assert ir_plain == Path(str(out) + ".ir.json").read_text()
    # No debug noise on stdout either way.
    assert proc_debug.stdout == proc_plain.stdout


def test_pysc_debug_env_enables(tmp_path: Path) -> None:
    env = {**ENV, "PYSC_DEBUG": "1"}
    proc = run_cli(
        "compile",
        str(EXAMPLES / "sum_squares.py"),
        "--emit",
        "ir",
        "-o",
        str(tmp_path / "m.ir.json"),
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "[debug] stage" in proc.stderr


def test_debug_fences_listed(tmp_path: Path) -> None:
    proc = run_cli("compile", str(EXAMPLES / "fence_pickle.py"), "--emit", "ir", "--debug=fences", "--skip-check")
    assert proc.returncode == 3
    assert "[debug] fences: 2" in proc.stderr
    assert "PS2002" in proc.stderr


def test_debug_rejects_unknown_topic() -> None:
    proc = run_cli("compile", str(EXAMPLES / "sum_squares.py"), "--emit", "ir", "--debug=bogus")
    assert proc.returncode == 1
    assert "unknown debug topic" in proc.stderr


def test_timings_flag_prints_stage_table(tmp_path: Path) -> None:
    out = tmp_path / "main.ll"
    proc = run_cli("compile", str(EXAMPLES / "sum_squares.py"), "--emit", "llvm", "-o", str(out), "--timings")
    assert proc.returncode == 0, proc.stderr
    assert "[debug] stage" in proc.stderr
    assert "[debug] parse" in proc.stderr
    assert "[debug] lower" in proc.stderr
    assert "[debug] check" in proc.stderr
    assert "[debug] total" in proc.stderr
    # parse is the part of the lower stage that went into the parser:
    # the lower row must not repeat it.
    lower = float(next(line for line in proc.stderr.splitlines() if line.startswith("[debug] lower")).split()[-1])
    assert lower > 0.0


def test_timings_no_debug_topics_beyond_stages(tmp_path: Path) -> None:
    out = tmp_path / "main.ll"
    proc = run_cli("compile", str(EXAMPLES / "sum_squares.py"), "--emit", "llvm", "-o", str(out), "--timings")
    assert proc.returncode == 0, proc.stderr
    assert "fences:" not in proc.stderr
    assert "[debug] cc:" not in proc.stderr


def test_summary_json_carries_timings_field(tmp_path: Path) -> None:
    out = tmp_path / "main.bin"
    dbg = tmp_path / "dbg"
    proc = run_cli(
        "compile", str(EXAMPLES / "sum_squares.py"), "--emit", "bin", "-o", str(out), "--debug-dir", str(dbg)
    )
    assert proc.returncode == 0, proc.stderr
    timings = json.loads((dbg / "summary.json").read_text())["timings"]
    for stage in ("parse", "lower", "check", "validate", "emit_llvm", "cc_compile", "cc_link", "link"):
        assert stage in timings, timings
        assert timings[stage] >= 0.0, timings


def test_timings_without_debug_stays_quiet(tmp_path: Path) -> None:
    out = tmp_path / "main.ll"
    proc = run_cli("compile", str(EXAMPLES / "sum_squares.py"), "--emit", "llvm", "-o", str(out))
    assert proc.returncode == 0, proc.stderr
    assert "[debug]" not in proc.stderr
