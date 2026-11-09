"""The bench delta table compares two recorded runs and reports only."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.baseline.delta import delta_table, load_report, main, ratio_text


def _row(name: str, ms: float, rss: int, spread: float | None = None) -> dict:
    row = {
        "name": name,
        "status": "ok",
        "native_median_ms": ms,
        "cpython_median_ms": 100.0,
        "time_ratio": ms / 100.0,
        "native_rss_kb": rss,
        "cpython_rss_kb": 1000,
        "rss_ratio": rss / 1000,
        "binary_bytes": 4096,
        "samples": 20,
    }
    if spread is not None:
        row["native_ms_spread"] = {"p25_ms": ms * 0.9, "p75_ms": ms * 1.1, "iqr_over_median": spread}
    return row


def _report(rows: list[dict], **extra) -> dict:
    report = {
        "schema": 1,
        "recorded_at": "2026-11-04T00:00:00Z",
        "cpython": "3.14.0",
        "runs": 20,
        "workloads": rows,
        "geomean_group": "kernel",
        "geomean_time_ratio": 0.1,
        "geomean_rss_ratio": 0.2,
    }
    report.update(extra)
    return report


def test_delta_table_reports_time_rss_and_spread() -> None:
    base = _report([_row("sum-squares", 2.0, 2000, spread=0.1)])
    head = _report([_row("sum-squares", 2.5, 2500, spread=0.2)])
    table = delta_table(base, head)
    assert "| sum-squares | 2.000 | 2.500 | 1.250x slower |" in table
    assert "| 2000 | 2500 | 1.250x slower | 0.1000 | 0.2000 |" in table
    assert "0.1000 | 0.2000 |" in table
    assert table.startswith("### Benchmarks: delta against the previous run")


def test_delta_table_flags_new_and_dropped_workloads() -> None:
    base = _report([_row("gone", 1.0, 100)])
    head = _report([_row("fresh", 1.0, 100)])
    table = delta_table(base, head)
    assert "new in head" in table
    assert "dropped in head" in table


def test_delta_table_marks_a_failed_workload() -> None:
    bad = _row("sum-squares", 1.0, 100)
    bad["status"] = "mismatch"
    table = delta_table(_report([bad]), _report([_row("sum-squares", 1.0, 100)]))
    assert "not ok (mismatch/ok)" in table


def test_delta_table_tolerates_a_report_without_spread() -> None:
    base = _report([_row("sum-squares", 2.0, 2000)])
    head = _report([_row("sum-squares", 2.0, 2000, spread=0.1)])
    table = delta_table(base, head)
    assert "| 2.000 | 2.000 | 1.000x same |" in table
    assert "0.1000" in table


def test_delta_table_compares_the_geomean_rows() -> None:
    base = _report([], geomean_time_ratio=0.10)
    head = _report([], geomean_time_ratio=0.125)
    table = delta_table(base, head)
    assert "| geomean (kernel) | 0.1000 | 0.1250 | 1.250x slower |" in table


def test_ratio_text() -> None:
    assert ratio_text(2.0, 1.0) == "0.500x faster"
    assert ratio_text(2.0, 2.0) == "1.000x same"
    assert ratio_text(0.0, 1.0) == "?"


def test_main_writes_the_table_and_a_summary(tmp_path: Path, capsys) -> None:
    base = tmp_path / "base.json"
    head = tmp_path / "head.json"
    base.write_text(json.dumps(_report([_row("sum-squares", 2.0, 2000)])), encoding="utf-8")
    head.write_text(json.dumps(_report([_row("sum-squares", 2.5, 2500)])), encoding="utf-8")
    summary = tmp_path / "summary.md"
    code = main([str(base), str(head), "--summary", str(summary)])
    assert code == 0
    out = capsys.readouterr().out
    assert "1.250x slower" in out
    assert "1.250x slower" in summary.read_text(encoding="utf-8")


def test_main_refuses_a_file_that_is_not_a_report(tmp_path: Path, capsys) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    code = main([str(bad), str(bad)])
    assert code == 2
    assert "not a baseline report" in capsys.readouterr().err


def test_load_report_reads_a_recorded_file(tmp_path: Path) -> None:
    path = tmp_path / "bench.json"
    path.write_text(json.dumps(_report([_row("sum-squares", 2.0, 2000)])), encoding="utf-8")
    assert load_report(str(path))["workloads"][0]["name"] == "sum-squares"
