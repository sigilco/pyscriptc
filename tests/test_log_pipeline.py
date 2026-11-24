# SPDX-License-Identifier: Apache-2.0
"""The log pipeline reference app (issue #498).

`examples/log_pipeline.py` reads a CSV of access logs, aggregates by status
class and by path, computes percentile latencies and writes a JSON report.
The shapes pinned here:

- the app and the committed fixture compile with 0 fences,
- the compiled binary prints the same report as CPython on the fixture,
  byte for byte (key order and float formatting),
- the report is written to a file when a second path is given,
- the committed differential probe
  `probes/differential/log_pipeline.py` is byte-identical to the pinned
  CPython 3.14.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import ROOT

pytestmark = pytest.mark.skipif(shutil.which("zig") is None, reason="zig not on PATH")

APP = ROOT / "examples" / "log_pipeline.py"
FIXTURE = ROOT / "examples" / "access_logs.csv"
PROBE = ROOT / "probes" / "differential" / "log_pipeline.py"
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=ENV,
    )


def test_fixture_is_a_few_thousand_rows() -> None:
    lines = FIXTURE.read_text().splitlines()
    assert lines[0] == "timestamp,method,path,status,bytes,latency_ms"
    assert 2000 <= len(lines) - 1 <= 10000
    assert all(len(line.split(",")) == 6 for line in lines)


def test_app_compiles_with_zero_fences() -> None:
    got = _cli("coverage", str(APP))
    assert got.returncode == 0, got.stderr
    assert "0 fences" in got.stdout


def test_app_matches_cpython_byte_for_byte(tmp_path: Path) -> None:
    args = json.dumps([str(FIXTURE)])
    built = _cli("compile", str(APP), "--run", "--call", "pipeline", "--args", args, "-o", str(tmp_path / "pipeline"))
    assert built.returncode == 0, built.stderr
    sys.path.insert(0, str(APP.parent))
    try:
        import log_pipeline

        expected = log_pipeline.pipeline(str(FIXTURE)) + "\n"
    finally:
        sys.path.remove(str(APP.parent))
    assert built.stdout == expected
    report = json.loads(expected)
    assert report["total_records"] == 4000
    assert list(report) == ["total_records", "total_bytes", "status_classes", "paths", "latencies"]
    assert list(report["latencies"]) == ["p50", "p90", "p95", "p99"]


def test_app_writes_the_report_to_a_file(tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    args = json.dumps([str(FIXTURE), str(out)])
    built = _cli("compile", str(APP), "--run", "--call", "pipeline", "--args", args, "-o", str(tmp_path / "pipeline"))
    assert built.returncode == 0, built.stderr
    assert out.read_text() + "\n" == built.stdout


def test_the_committed_probe_matches_cpython(tmp_path: Path) -> None:
    """The differential probe byte-compares stdout, stderr and the exit code."""
    assert PROBE.is_file()
    built = _cli("compile", str(PROBE), "--emit", "bin", "-o", str(tmp_path / "probe"), "--run")
    oracle = subprocess.run([sys.executable, str(PROBE)], cwd=ROOT, capture_output=True, text=True)
    assert oracle.returncode == built.returncode == 0, built.stderr
    assert oracle.stdout == built.stdout
    assert oracle.stderr == built.stderr
