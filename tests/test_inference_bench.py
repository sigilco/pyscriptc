# SPDX-License-Identifier: Apache-2.0
"""The compile-time inference benchmark suite (issue #1319).

The suite prices the pin engine on three in-repo trees: the annotated
`examples/wordcount` (the skip fast path must take the engine out), the
39-module `benchmarks/real_scripts/scripts` tree, and a generated
~150-module chain that keeps the suite hermetic.

The suite is report-only. The test asserts the properties that make it
usable as a release receipt and safe to leave unarmed: the rows carry
the engine's own report, the wordcount row shows the engine skipped,
and the runner refuses `--record` and `--check` so no reference file can
ever gate on a number that two releases of data have not armed yet.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from benchmarks.baseline.delta import inference_delta_table, inference_table
from benchmarks.baseline.inference import suite_workloads
from benchmarks.baseline.synthetic import MODULE_COUNT, module_source, write_tree

ROOT = Path(__file__).resolve().parents[1]


def test_suite_workloads_are_the_three_in_repo_trees() -> None:
    workloads = suite_workloads()
    names = [workload["name"] for workload in workloads]
    assert names == ["wordcount-infer", "real-scripts-infer", "synthetic-150-infer"]
    sources = {workload["name"]: workload["source"] for workload in workloads}
    assert sources["wordcount-infer"] == str(ROOT / "examples" / "wordcount")
    assert sources["real-scripts-infer"] == str(ROOT / "benchmarks" / "real_scripts" / "scripts")
    assert sources["synthetic-150-infer"] is None


def test_synthetic_tree_is_generated_at_the_expected_size(tmp_path: Path) -> None:
    root = write_tree(tmp_path / "synthetic")
    modules = sorted(root.glob("mod_*.py"))
    assert len(modules) == MODULE_COUNT
    assert (root / "__main__.py").is_file()
    # The first module has no predecessor to import.
    assert "import" not in module_source(0)
    assert "from mod_000 import step_000" in module_source(1)
    # Every module holds one unannotated parameter and one annotated
    # caller, so the engine has a pin to prove per module.
    assert "def step_001(value):" in module_source(1)
    assert "def use_001(count: int) -> int:" in module_source(1)


def test_inference_suite_refuses_record_and_check(capsys: pytest.CaptureFixture[str]) -> None:
    from benchmarks.baseline.run import main

    for flag in ("--record", "--check"):
        code = main(["--suite", "inference", flag])
        assert code == 2
        captured = capsys.readouterr()
        assert "report-only" in captured.err


def test_inference_rows_carry_the_engine_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from benchmarks.baseline import inference

    calls: list[dict[str, object]] = []

    def _fake_compile(source: Path, *, infer: bool, out: Path, debug_dir: Path) -> tuple[float, object]:
        calls.append({"infer": infer, "source": source})
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / "summary.json").write_text(
            json.dumps(
                {
                    "status": "ok",
                    "infer": {"skipped": 0 if infer else None, "pins": 3, "callSites": 4, "ms": 12},
                }
            ),
            encoding="utf-8",
        )
        return 100.0, subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(inference, "_compile", _fake_compile)
    rows = inference.run_inference_suite()
    assert [row["name"] for row in rows] == [w["name"] for w in suite_workloads()]
    for row in rows:
        assert row["status"] == "ok"
        assert row["infer_pins"] == 3
        assert row["infer_call_sites"] == 4
        assert row["infer_engine_ms"] == 12
        assert row["infer_overhead_ratio"] == pytest.approx(1.0)
    # Each workload is compiled twice: once plain, once with inference.
    assert len(calls) == 6
    assert sum(1 for call in calls if call["infer"]) == 3


def test_inference_delta_tables_are_report_only() -> None:
    base = {
        "recorded_at": "2026-11-18T00:00:00Z",
        "workloads": [
            {
                "name": "wordcount-infer",
                "status": "ok",
                "compile_ms": 100.0,
                "infer_ms": 200.0,
                "infer_overhead_ratio": 2.0,
                "infer_skipped": 1,
                "infer_pins": 0,
                "infer_call_sites": 0,
                "infer_engine_ms": 1,
            }
        ],
    }
    head = {
        "recorded_at": "2026-11-19T00:00:00Z",
        "workloads": [
            {
                "name": "wordcount-infer",
                "status": "ok",
                "compile_ms": 100.0,
                "infer_ms": 400.0,
                "infer_overhead_ratio": 4.0,
                "infer_skipped": 1,
                "infer_pins": 0,
                "infer_call_sites": 0,
                "infer_engine_ms": 1,
            }
        ],
    }
    table = inference_table(head)
    assert "wordcount-infer" in table
    assert "skipped" in table
    delta = inference_delta_table(base, head)
    assert "wordcount-infer" in delta
    assert "2.000x slower" in delta


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_inference_suite_runs_the_engine_on_the_wordcount_tree() -> None:
    """The annotated tree must skip the engine, and the run must report it."""

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.baseline.run",
            "--suite",
            "inference",
            "--json",
            "/tmp/pyscriptc-infer-bench-test.json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(Path("/tmp/pyscriptc-infer-bench-test.json").read_text(encoding="utf-8"))
    assert report["suite"] == "inference"
    rows = {row["name"]: row for row in report["workloads"]}
    wordcount = rows["wordcount-infer"]
    assert wordcount["status"] == "ok"
    # The skip fast path (issue #1317): the engine never starts for a
    # fully annotated tree, so its own wall time stays at the AST walk.
    assert wordcount["infer_skipped"] == 1
    assert wordcount["infer_engine_ms"] < 50
    assert wordcount["infer_ms"] > wordcount["compile_ms"]
    # The real tree carries pins, so the engine runs there.
    assert rows["real-scripts-infer"]["infer_skipped"] == 0
    assert rows["real-scripts-infer"]["infer_pins"] > 0
