"""Every timed stdlib workload matches CPython before it is allowed into the baseline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.test_gates import ROOT, oracle, run_cli

pytestmark = pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh

CATALOG = json.loads((ROOT / "benchmarks" / "runtime" / "workloads.json").read_text(encoding="utf-8"))
LOWERED = [row for row in CATALOG["workloads"] if row.get("group") in {"stdlib", "island", "scripting"}]


@pytest.mark.parametrize("workload", LOWERED, ids=lambda row: row["name"])
def test_lowered_workload_matches_cpython(tmp_path: Path, workload: dict[str, object]) -> None:
    source = ROOT / "benchmarks" / "runtime" / str(workload["entry"])
    args = repr(workload["args"])
    got = run_cli(
        "compile",
        str(source),
        "--run",
        "--call",
        str(workload["call"]),
        "--args",
        args,
        "-o",
        str(tmp_path / str(workload["name"])),
    )
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(source, str(workload["call"]), args)
