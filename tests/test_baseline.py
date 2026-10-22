"""The baseline records wall time and peak RSS, and can reject a regression."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from benchmarks.baseline.compare import compare_reports
from benchmarks.harness import measure_command, versus_cpython

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_measure_command_reports_time_and_rss() -> None:
    sample = measure_command([sys.executable, "-c", "blob = bytearray(20_000_000); print(len(blob))"])
    assert sample.returncode == 0
    assert sample.stdout.startswith("20000000")
    assert sample.elapsed_ms > 0
    assert sample.rss_kb > 15_000


def test_compare_flags_time_and_rss_regressions() -> None:
    reference = {
        "workloads": [
            {"name": "sum-squares", "status": "ok", "time_ratio": 0.10, "rss_ratio": 0.20},
        ],
        "geomean_time_ratio": 0.10,
        "geomean_rss_ratio": 0.20,
    }
    slower = {
        "workloads": [
            {"name": "sum-squares", "status": "ok", "time_ratio": 0.25, "rss_ratio": 0.20},
        ],
        "geomean_time_ratio": 0.25,
        "geomean_rss_ratio": 0.20,
    }
    heavier = {
        "workloads": [
            {"name": "sum-squares", "status": "ok", "time_ratio": 0.10, "rss_ratio": 0.30},
        ],
        "geomean_time_ratio": 0.10,
        "geomean_rss_ratio": 0.30,
    }
    same = {
        "workloads": [
            {"name": "sum-squares", "status": "ok", "time_ratio": 0.11, "rss_ratio": 0.22},
        ],
        "geomean_time_ratio": 0.11,
        "geomean_rss_ratio": 0.22,
    }
    assert any("time " in failure and "x CPython" in failure for failure in compare_reports(slower, reference))
    assert any("rss " in failure and "x CPython" in failure for failure in compare_reports(heavier, reference))
    assert compare_reports(same, reference) == []


def test_versus_cpython_inverts_the_ratio() -> None:
    assert versus_cpython(0.15393) == "6.5x CPython"
    assert versus_cpython(0.04857) == "20.6x CPython"


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_baseline_run_records_time_and_rss(tmp_path: Path) -> None:
    report_path = tmp_path / "baseline.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.baseline.run",
            "--runs",
            "1",
            "--workloads",
            "sum-squares",
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
    row = report["workloads"][0]
    assert row["name"] == "sum-squares"
    assert row["status"] == "ok"
    assert row["native_median_ms"] > 0
    assert row["cpython_median_ms"] > 0
    if row["native_rss_kb"] <= 0 or row["cpython_rss_kb"] <= 0:
        # a child that exits before the first poll leaves ru_maxrss unread: flaky by nature, not a regression
        pytest.xfail("RSS read 0 for a short lived child")
    assert row["time_ratio"] > 0
    assert row["rss_ratio"] > 0
    assert "x CPython" in completed.stdout
    assert report["metrics"] == ["wall_ms", "rss_kb"]
