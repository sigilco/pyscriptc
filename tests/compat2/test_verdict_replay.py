"""A5 replay: one kept iteration, one drop, optional-tagged-class, and the injected bug."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from benchmarks.baseline import replay_verdict
from benchmarks.baseline.run import MACOS_ARM64_REFERENCE, REFERENCE, main, reference_for_host, refuses_macos_record

ROOT = Path(__file__).resolve().parents[2]


def test_replay_matches_expected_keep_and_drop() -> None:
    cases = {case["name"]: case for case in replay_verdict.replay(ROOT)}
    assert list(cases) == ["kept", "dropped", "optional-tagged-class", "injected-wrong-output"]

    kept = cases["kept"]
    assert kept["verdict"] == "PASS"
    assert kept["findings"] == []
    assert kept["primary_delta"] > 0
    assert kept["census_delta"] == 0
    assert kept["id"] == "046-bool-const-surface.json"
    movement = sum(
        kept["candidate_numerators"][key] - kept["baseline_numerators"][key] for key in replay_verdict.KPI_KEYS
    )
    assert kept["primary_delta"] == movement

    dropped = cases["dropped"]
    assert dropped["verdict"] == "FAIL"
    assert dropped["area"] == "progress"
    assert dropped["primary_delta"] == 0
    assert dropped["census_delta"] == 0
    assert dropped["baseline_numerators"] == dropped["candidate_numerators"]
    assert dropped["id"] == "020-nan-in.json"

    optional = cases["optional-tagged-class"]
    assert optional["verdict"] == "FAIL"
    assert optional["area"] == "progress"
    assert optional["behavior"] == "optional-tagged-class"
    assert optional["primary_delta"] == 0
    assert optional["census_delta"] == 0
    assert "No numerator moved" in optional["why"]
    assert optional["baseline_numerators"] == optional["candidate_numerators"]

    injected = cases["injected-wrong-output"]
    assert injected["verdict"] == "FAIL"
    assert injected["findings"][0]["area"] == "soundness:inject.floordiv"
    assert injected["findings"][0]["severity"] == "P0"


def test_replay_reads_numerators_from_the_historical_files() -> None:
    cases = {case["name"]: case for case in replay_verdict.replay(ROOT)}
    iterations = ROOT / "benchmarks" / "compat" / "iterations"
    before = replay_verdict.kpi_numerators(
        json.loads((iterations / "045-str-const-surface.json").read_text(encoding="utf-8"))
    )
    after = replay_verdict.kpi_numerators(
        json.loads((iterations / "046-bool-const-surface.json").read_text(encoding="utf-8"))
    )
    assert cases["kept"]["baseline_numerators"] == before
    assert cases["kept"]["candidate_numerators"] == after
    assert after["stdlib_surface"] > before["stdlib_surface"]

    failed = replay_verdict.load_failed(ROOT / "benchmarks" / "compat" / "failed.jsonl")
    nan = replay_verdict.failed_record(failed, "model-nan-in via == on NaN bits")
    optional = replay_verdict.failed_record(failed, "optional-tagged-class")
    recorded_drop = {key: nan["numerators"][key] for key in replay_verdict.KPI_KEYS}
    assert cases["dropped"]["candidate_numerators"] == recorded_drop
    assert cases["optional-tagged-class"]["candidate_numerators"] == {
        key: optional["numerators"][key] for key in replay_verdict.KPI_KEYS
    }
    assert "019-float-inf.json" in nan["why"]
    assert cases["dropped"]["source"] == "benchmarks/compat/failed.jsonl"
    assert cases["optional-tagged-class"]["source"] == "benchmarks/compat/failed.jsonl"


def test_replay_script_prints_the_four_outcomes() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "benchmarks.baseline.replay_verdict"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    text = completed.stdout
    assert "kept: PASS" in text
    assert "dropped: FAIL" in text
    assert "area=progress" in text
    assert "optional-tagged-class: FAIL" in text
    assert "behavior=optional-tagged-class" in text
    assert "injected-wrong-output: FAIL" in text
    assert "area=soundness:inject.floordiv" in text
    assert "No macOS timings were recorded." in text


def test_macos_arm64_reference_is_a_separate_slot() -> None:
    assert reference_for_host("Linux", "x86_64") == REFERENCE
    assert reference_for_host("Darwin", "arm64") == MACOS_ARM64_REFERENCE
    assert reference_for_host("Darwin", "x86_64") == REFERENCE
    assert MACOS_ARM64_REFERENCE.name == "reference-macos-arm64.json"
    assert REFERENCE.name == "reference.json"
    assert not refuses_macos_record(str(REFERENCE), "Linux", "x86_64")
    assert refuses_macos_record(str(MACOS_ARM64_REFERENCE), "Linux", "x86_64")
    assert not refuses_macos_record(str(MACOS_ARM64_REFERENCE), "Darwin", "arm64")

    code = main(["--record", str(MACOS_ARM64_REFERENCE), "--runs", "1", "--workloads", "sum-squares"])
    assert code == 2
    assert not MACOS_ARM64_REFERENCE.exists()
    slot = replay_verdict.baseline_slot_status()
    assert slot["linux_reference_present"] is True
    assert slot["macos_timings_recorded"] is False
