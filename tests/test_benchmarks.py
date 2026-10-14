"""The benchmark runners match CPython and refuse to time a fence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_matches_cpython_and_records_fence(tmp_path: Path) -> None:
    report_path = tmp_path / "runtime.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.runtime.run",
            "--runs",
            "1",
            "--workloads",
            "sum-squares,fenced-strip",
            "--json",
            str(report_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    rows = {row["name"]: row for row in report["workloads"]}
    assert rows["sum-squares"]["status"] == "ok"
    assert rows["sum-squares"]["ratio"] > 0
    assert rows["fenced-strip"]["status"] == "fence"
    assert "geomean_vs_cpython" in report
    assert "x CPython" in completed.stdout


def test_scaling_sum_squares_matches_cpython(tmp_path: Path) -> None:
    report_path = tmp_path / "scaling.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.scaling.run",
            "--only",
            "numeric:sum_squares",
            "--sizes",
            "20,200",
            "--reps",
            "1",
            "--json",
            str(report_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report[0]["case"] == "sum_squares"
    assert report[0]["flags"] == []
    assert all(row.get("native_ms", 0) > 0 for row in report[0]["rows"])
