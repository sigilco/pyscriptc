"""Tests for the must-fence adversarial probe corpus and soundness ratchet gate."""

from __future__ import annotations

from typing import Any

import pytest
from benchmarks.compat2 import verdict
from benchmarks.compat2.must_fence import evaluate_case, load_cases

ALL_EMITTED_CODES = frozenset(
    {
        "PS1001",
        "PS1003",
        "PS1004",
        "PS1101",
        "PS1102",
        "PS1103",
        "PS1104",
        "PS1105",
        "PS1106",
        "PS1201",
        "PS1202",
        "PS1203",
        "PS1204",
        "PS1205",
        "PS1206",
        "PS2001",
        "PS2002",
        "PS2003",
        "PS2004",
        "PS2005",
        "PS4101",
        "PS4201",
        "PS4202",
        "PS4203",
        "PS4204",
    }
)


def test_corpus_loads_cases() -> None:
    cases = load_cases()
    assert len(cases) >= 50
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case IDs in corpus"


def test_must_fence_corpus_covers_all_emitted_codes() -> None:
    cases = load_cases()
    covered = {c["code"] for c in cases if c.get("code")}
    missing = ALL_EMITTED_CODES - covered
    assert not missing, f"Missing must-fence cases for emitted codes: {sorted(missing)}"


def test_must_fence_corpus_covers_all_tiers() -> None:
    cases = load_cases()
    tiers = {c.get("tier") for c in cases}
    assert {"T0", "T1", "T2", "T3"} <= tiers


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["id"])
def test_must_fence_case(case: dict[str, Any]) -> None:
    result = evaluate_case(case, timeout=60.0)
    assert result["held"] is True, f"Must-fence probe {case['id']} failed: {result['evidence']}"
    assert result["outcome"] == "pass"


def test_deliberately_wrong_fixture_triggers_soundness_gate() -> None:
    """A seeded deliberately-wrong fixture (construct stops refusing) proves the gate fires P0."""
    fake_case = {
        "id": "t0.injected_silently_compiles",
        "tier": "T0",
        "construct": "eval dynamic code execution",
        "code": "PS2002",
        "expected": "fence",
    }
    # Simulate a soundness failure: the construct stopped refusing and silently compiled
    broken_result = {
        "id": fake_case["id"],
        "tier": fake_case["tier"],
        "construct": fake_case["construct"],
        "expected": "fence",
        "expected_code": fake_case["code"],
        "outcome": "wrong-output",
        "held": False,
        "evidence": "silently compiled (rc=0), expected refusal PS2002",
        "compile_s": 0.05,
    }
    must_fence_summary = {
        "passed": 0,
        "failed": 1,
        "total": 1,
        "failures": [broken_result],
        "rows": [
            {
                "id": f"must_fence:{fake_case['id']}",
                "area": "must_fence",
                "category": "language",
                "outcome": "wrong-output",
                "code": "PS2002",
                "evidence": broken_result["evidence"],
            }
        ],
    }

    base = {
        "rows": [
            {
                "id": f"must_fence:{fake_case['id']}",
                "area": "must_fence",
                "category": "language",
                "outcome": "pass",
                "code": "PS2002",
                "evidence": "refused with PS2002",
            }
        ],
        "weights": {f"must_fence:{fake_case['id']}": 1.0},
        "compile_s": 0.1,
    }
    cand = {
        "rows": must_fence_summary["rows"],
        "weights": {f"must_fence:{fake_case['id']}": 1.0},
        "compile_s": 0.1,
        "must_fence": must_fence_summary,
    }

    # 1. Gate fires when passed via must_fence keyword
    out1 = verdict.verdict(base, cand, must_fence=must_fence_summary, skip_perf=True)
    assert out1["verdict"] == "FAIL"
    p0_findings = [f for f in out1["findings"] if f["severity"] == "P0"]
    assert len(p0_findings) >= 1
    assert any("soundness:must_fence:t0.injected_silently_compiles" in f["area"] for f in p0_findings)

    # 2. Gate fires when passed inside candidate dictionary
    out2 = verdict.verdict(base, cand, skip_perf=True)
    assert out2["verdict"] == "FAIL"
    p0_findings_cand = [f for f in out2["findings"] if f["severity"] == "P0"]
    assert any("soundness:must_fence:t0.injected_silently_compiles" in f["area"] for f in p0_findings_cand)

    # 3. Soundness ratchet fires directly on cand rows with area must_fence
    out3 = verdict.verdict(base, {"rows": must_fence_summary["rows"], "weights": {}}, skip_perf=True)
    assert out3["verdict"] == "FAIL"
    p0_soundness = [f for f in out3["findings"] if f["severity"] == "P0"]
    assert any(fake_case["id"] in f["area"] for f in p0_soundness)
