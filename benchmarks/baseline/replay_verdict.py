"""Replay three historical v1 iterations and the injected-bug drop through the verdict.

The older strategy plan named in AGENTS.md is not in this clone. Expected
outcomes are the A5 row of ``.agents/plans/2026-10-14-waves-after-scorer-v2.md``:

1. one kept historical iteration → PASS
2. one dropped historical iteration → FAIL
3. ``optional-tagged-class`` → FAIL
4. injected wrong-output → FAIL on the soundness gate

v1 numerators are read from ``benchmarks/compat/iterations`` and
``benchmarks/compat/failed.jsonl``. ``.agents/docs/loop.md`` defines a numerator
as ``scores[area].lowered`` for the first four KPIs and ``scores.top100.subset``
(falling back to ``lowered_or_subset``). Each KPI becomes one compat2 row whose
weight is that integer, so ``weighted_pass`` equals the sum of the five
numerators and the primary delta equals the historical movement. v1 snapshots
record no ``compile_s``, so both sides use 0 and the compile-time gate stays
quiet. Census files are omitted, so a flat pair is a progress FAIL.

The four replays call ``verdict(..., skip_perf=True)``. The perf gate is the
separate ``python -m benchmarks.baseline.run --check`` against the existing
Linux ``reference.json``.

    uv run python -m benchmarks.baseline.replay_verdict
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from benchmarks.baseline.run import MACOS_ARM64_REFERENCE, REFERENCE
from benchmarks.compat2 import verdict

KPI_KEYS = ("language", "builtins", "stdlib_modules", "stdlib_surface", "top100")

# Kept: the last snapshot whose numerators rose (stdlib_surface).
KEPT_BEFORE = "045-str-const-surface.json"
KEPT_AFTER = "046-bool-const-surface.json"
# Dropped: failed.jsonl says this snapshot's numerators match the previous one.
DROPPED_BEHAVIOR = "model-nan-in via == on NaN bits"
DROPPED_BEFORE = "019-float-inf.json"
DROPPED_AFTER = "020-nan-in.json"
# Third case: reverted, and the failed line says no numerator moved.
OPTIONAL_BEHAVIOR = "optional-tagged-class"
OPTIONAL_BASELINE = "046-bool-const-surface.json"
INJECTED_ID = "inject.floordiv"
INJECTED_SOURCE = "tests/compat2/test_scorer.py::test_injected_wrong_output_is_soundness_fail"

EXPECTED = {
    "kept": "PASS",
    "dropped": "FAIL",
    "optional-tagged-class": "FAIL",
    "injected-wrong-output": "FAIL",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def kpi_numerators(snapshot: dict[str, Any]) -> dict[str, int]:
    """The five integers loop.md tells an iteration to copy from a snapshot."""

    scores = snapshot["scores"]
    out: dict[str, int] = {}
    for key in KPI_KEYS:
        block = scores[key]
        if key == "top100":
            if "subset" in block:
                out[key] = int(block["subset"])
            else:
                out[key] = int(block["lowered_or_subset"])
        else:
            out[key] = int(block["lowered"])
    return out


def load_snapshot(iterations: Path, name: str) -> dict[str, Any]:
    return json.loads((iterations / name).read_text(encoding="utf-8"))


def load_failed(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: {exc}") from exc
    return rows


def failed_record(rows: list[dict[str, Any]], behavior: str) -> dict[str, Any]:
    matches = [row for row in rows if row.get("behavior") == behavior]
    if len(matches) != 1:
        raise ValueError(f"expected one failed.jsonl behavior {behavior!r}, found {len(matches)}")
    return matches[0]


def score_from_numerators(nums: dict[str, int], source: str) -> dict[str, Any]:
    """One pass row per KPI. The weight is the numerator read from ``source``."""

    rows = []
    weights: dict[str, float] = {}
    for key in KPI_KEYS:
        row_id = f"v1.{key}"
        value = int(nums[key])
        rows.append(
            {
                "id": row_id,
                "outcome": "pass",
                "evidence": f"{key}={value} from {source}",
            }
        )
        weights[row_id] = float(value)
    # v1 snapshots have no compile_s. Zero keeps the compile-time gate quiet.
    return {"rows": rows, "weights": weights, "compile_s": 0.0}


def _require_equal(left: dict[str, int], right: dict[str, int], label: str) -> None:
    if left != right:
        raise ValueError(f"{label}: {left} != {right}")


def _movement(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {key: after[key] - before[key] for key in KPI_KEYS}


def _case(
    *,
    name: str,
    ident: str,
    behavior: str,
    source: str,
    why: str,
    baseline_numerators: dict[str, int] | None,
    candidate_numerators: dict[str, int] | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    findings = result["findings"]
    return {
        "name": name,
        "id": ident,
        "behavior": behavior,
        "source": source,
        "why": why,
        "baseline_numerators": baseline_numerators,
        "candidate_numerators": candidate_numerators,
        "verdict": result["verdict"],
        "primary_delta": result["primary_delta"],
        "census_delta": result["census_delta"],
        "findings": findings,
        "area": findings[0]["area"] if findings else "",
    }


def _judge(base: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any]:
    return verdict.verdict(base, cand, skip_perf=True)


def kept_case(iterations: Path) -> dict[str, Any]:
    before_name = KEPT_BEFORE
    after_name = KEPT_AFTER
    before = kpi_numerators(load_snapshot(iterations, before_name))
    after = kpi_numerators(load_snapshot(iterations, after_name))
    delta = _movement(before, after)
    if not any(value > 0 for value in delta.values()):
        raise ValueError(f"{before_name} -> {after_name} did not raise a numerator: {delta}")
    if any(value < 0 for value in delta.values()):
        raise ValueError(f"{before_name} -> {after_name} shrank a numerator: {delta}")
    before_src = f"benchmarks/compat/iterations/{before_name}"
    after_src = f"benchmarks/compat/iterations/{after_name}"
    result = _judge(score_from_numerators(before, before_src), score_from_numerators(after, after_src))
    return _case(
        name="kept",
        ident=after_name,
        behavior="",
        source=f"{before_src} -> {after_src}",
        why="",
        baseline_numerators=before,
        candidate_numerators=after,
        result=result,
    )


def dropped_case(iterations: Path, failed: list[dict[str, Any]]) -> dict[str, Any]:
    record = failed_record(failed, DROPPED_BEHAVIOR)
    if record.get("iteration") != DROPPED_AFTER:
        raise ValueError(f"{DROPPED_BEHAVIOR} iteration is {record.get('iteration')!r}, expected {DROPPED_AFTER}")
    if DROPPED_BEFORE not in record["why"]:
        raise ValueError(f"{DROPPED_BEHAVIOR} why does not cite {DROPPED_BEFORE}")
    recorded = {key: int(record["numerators"][key]) for key in KPI_KEYS}
    before = kpi_numerators(load_snapshot(iterations, DROPPED_BEFORE))
    after = kpi_numerators(load_snapshot(iterations, DROPPED_AFTER))
    _require_equal(recorded, before, f"failed.jsonl vs {DROPPED_BEFORE}")
    _require_equal(recorded, after, f"failed.jsonl vs {DROPPED_AFTER}")
    if any(value != 0 for value in _movement(before, after).values()):
        raise ValueError(f"{DROPPED_BEFORE} -> {DROPPED_AFTER} moved a numerator")
    source = "benchmarks/compat/failed.jsonl"
    result = _judge(score_from_numerators(before, source), score_from_numerators(after, source))
    return _case(
        name="dropped",
        ident=DROPPED_AFTER,
        behavior=DROPPED_BEHAVIOR,
        source=source,
        why=record["why"],
        baseline_numerators=before,
        candidate_numerators=after,
        result=result,
    )


def optional_case(iterations: Path, failed: list[dict[str, Any]]) -> dict[str, Any]:
    record = failed_record(failed, OPTIONAL_BEHAVIOR)
    if "No numerator moved" not in record["why"]:
        raise ValueError("optional-tagged-class why no longer says that no numerator moved")
    recorded = {key: int(record["numerators"][key]) for key in KPI_KEYS}
    baseline = kpi_numerators(load_snapshot(iterations, OPTIONAL_BASELINE))
    _require_equal(recorded, baseline, f"failed.jsonl vs {OPTIONAL_BASELINE}")
    source = "benchmarks/compat/failed.jsonl"
    # The attempt was reverted before a snapshot. Both sides are the numerators
    # the failed line copied, which match OPTIONAL_BASELINE.
    result = _judge(score_from_numerators(baseline, source), score_from_numerators(recorded, source))
    return _case(
        name="optional-tagged-class",
        ident=OPTIONAL_BEHAVIOR,
        behavior=OPTIONAL_BEHAVIOR,
        source=source,
        why=record["why"],
        baseline_numerators=baseline,
        candidate_numerators=recorded,
        result=result,
    )


def injected_case() -> dict[str, Any]:
    """Same transition as ``test_injected_wrong_output_is_soundness_fail``.

    The baseline row passes. The candidate row is ``wrong-output``, which is
    the soundness gate. Weight 1.0 is the language-probe default in
    ``benchmarks/compat2/outcomes.py``, not a v1 numerator.
    """

    evidence = f"same soundness shape as {INJECTED_SOURCE}"
    base = {
        "compile_s": 0.0,
        "weights": {INJECTED_ID: 1.0},
        "rows": [{"id": INJECTED_ID, "outcome": "pass", "evidence": evidence}],
    }
    cand = {
        "compile_s": 0.0,
        "weights": {INJECTED_ID: 1.0},
        "rows": [{"id": INJECTED_ID, "outcome": "wrong-output", "evidence": evidence}],
    }
    return _case(
        name="injected-wrong-output",
        ident=INJECTED_ID,
        behavior="injected wrong-output",
        source=INJECTED_SOURCE,
        why="a pass row becomes wrong-output and the soundness gate fails",
        baseline_numerators=None,
        candidate_numerators=None,
        result=_judge(base, cand),
    )


def replay(root: Path | None = None) -> list[dict[str, Any]]:
    root = repo_root() if root is None else root
    iterations = root / "benchmarks" / "compat" / "iterations"
    failed_path = root / "benchmarks" / "compat" / "failed.jsonl"
    failed = load_failed(failed_path)
    return [
        kept_case(iterations),
        dropped_case(iterations, failed),
        optional_case(iterations, failed),
        injected_case(),
    ]


def baseline_slot_status() -> dict[str, Any]:
    return {
        "linux_reference": "benchmarks/baseline/reference.json",
        "linux_reference_present": REFERENCE.is_file(),
        "macos_arm64_reference": "benchmarks/baseline/reference-macos-arm64.json",
        "macos_timings_recorded": MACOS_ARM64_REFERENCE.exists(),
    }


def format_case(case: dict[str, Any]) -> str:
    parts = [f"{case['name']}: {case['verdict']}", f"id={case['id']}"]
    if case["behavior"]:
        parts.append(f"behavior={case['behavior']}")
    parts.append(f"primary={case['primary_delta']:+.1f}")
    if case["area"]:
        parts.append(f"area={case['area']}")
    if case["baseline_numerators"] is not None and case["candidate_numerators"] is not None:
        before = ",".join(f"{key}={case['baseline_numerators'][key]}" for key in KPI_KEYS)
        after = ",".join(f"{key}={case['candidate_numerators'][key]}" for key in KPI_KEYS)
        parts.append(f"baseline={before}")
        parts.append(f"candidate={after}")
    parts.append(f"source={case['source']}")
    return "  ".join(parts)


def main(argv: list[str] | None = None) -> int:
    if argv:
        print(f"unexpected arguments: {' '.join(argv)}", file=sys.stderr)
        return 2
    cases = replay()
    print("A5 replay through benchmarks.compat2.verdict (skip_perf=True)")
    failed = False
    for case in cases:
        print(format_case(case))
        if case["verdict"] != EXPECTED[case["name"]]:
            failed = True
    slot = baseline_slot_status()
    if slot["macos_timings_recorded"]:
        print("macOS timings are present in benchmarks/baseline/reference-macos-arm64.json")
    else:
        print("No macOS timings were recorded.")
    print(
        "perf gate: exercised separately with "
        "`uv run python -m benchmarks.baseline.run --check` against benchmarks/baseline/reference.json"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
