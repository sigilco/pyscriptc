"""Receipt-1: direct vs MIR lane perf ratio table (handoff 6)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from scripts.mir_perf_ratio import (
    LANE_GROUPS,
    REFERENCE,
    compare_lanes,
    load_fixed_set,
    main,
    render_markdown,
    spread,
)

ROOT = Path(__file__).resolve().parents[1]


def test_fixed_set_is_kernel_stdlib_scripting_only() -> None:
    rows = load_fixed_set()
    assert rows
    assert {row["group"] for row in rows} <= set(LANE_GROUPS)
    names = [row["name"] for row in rows]
    assert "sum-squares" in names
    assert "math-loop" in names
    assert "script-slice" in names
    assert "threads" not in names
    assert names == sorted(names) or names == [row["name"] for row in rows]


def test_fixed_set_skips_island_and_fence() -> None:
    names = [row["name"] for row in load_fixed_set()]
    assert "socket-host" not in names
    assert "fenced-strip" not in names


def test_spread_reports_iqr() -> None:
    info = spread([10.0, 12.0, 11.0, 13.0, 11.5], 11.5)
    assert info["p25_ms"] <= info["p75_ms"]
    assert info["iqr_over_median"] >= 0


def test_compare_lanes_uses_injected_compile_and_measure(tmp_path: Path) -> None:
    calls: list[bool] = []

    def compile_fn(source: Path, call: str, args: list[object], output: Path, mir: bool):
        calls.append(mir)
        output.write_text("bin", encoding="utf-8")
        return subprocess.CompletedProcess(["compile"], 0, "", "")

    samples = {"n": 0}

    def measure(command: list[str]):
        samples["n"] += 1
        # Alternate slightly so medians differ but stay in noise.
        elapsed = 10.0 + (samples["n"] % 2)
        return SimpleNamespace(returncode=0, elapsed_ms=elapsed, stderr="")

    workloads = [
        {
            "name": "sum-squares",
            "entry": "workloads/sum-squares.py",
            "call": "sum_squares",
            "args": [10],
            "group": "kernel",
        }
    ]
    report = compare_lanes(workloads, runs=3, compile_fn=compile_fn, measure=measure, tmp=tmp_path)
    assert calls == [False, True]
    assert report["runs"] == 3
    row = report["workloads"][0]
    assert row["status"] == "ok"
    assert row["ratio"] > 0
    assert "low" in row["ratio_ci95"]
    assert row["verdict"] in {"faster", "slower", "inconclusive"}
    table = render_markdown(report)
    assert "sum-squares" in table
    assert "ratio" in table
    assert "Report only" in table


def test_compile_error_is_a_row_not_a_crash(tmp_path: Path) -> None:
    def compile_fn(source: Path, call: str, args: list[object], output: Path, mir: bool):
        if mir:
            return subprocess.CompletedProcess(["compile"], 1, "", "mir refused")
        output.write_text("bin", encoding="utf-8")
        return subprocess.CompletedProcess(["compile"], 0, "", "")

    workloads = [
        {
            "name": "math-loop",
            "entry": "workloads/math_loop.py",
            "call": "bench",
            "args": [1],
            "group": "stdlib",
        }
    ]
    report = compare_lanes(workloads, runs=1, compile_fn=compile_fn, measure=lambda c: None, tmp=tmp_path)
    row = report["workloads"][0]
    assert row["status"] == "error"
    assert "mir refused" in row["detail"]
    table = render_markdown(report)
    assert "error:" in table


def test_does_not_write_frozen_baseline(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.mir_perf_ratio.compare_lanes",
        lambda workloads, runs: {
            "schema": 1,
            "recorded_at": "t",
            "runs": runs,
            "workloads": [],
        },
    )
    monkeypatch.setattr(
        "scripts.mir_perf_ratio.load_fixed_set",
        lambda names=None: [{"name": "sum-squares", "group": "kernel"}],
    )
    before = REFERENCE.stat().st_mtime
    json_out = tmp_path / "out.json"
    code = main(["--runs", "1", "--json-out", str(json_out), "--json"])
    assert code == 0
    assert json_out.is_file()
    assert REFERENCE.stat().st_mtime == before


def test_cli_help() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "mir_perf_ratio.py"), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ratio" in proc.stdout
    assert "--runs" in proc.stdout
