"""Lexicographic keep/drop verdict for one autoresearch iteration.

    python -m benchmarks.compat2.verdict --baseline base.json --candidate new.json
        [--census-before a.json --census-after b.json] [--ledger run-ledger.json]
        [--wrong-tracker known-wrong.json] [--wrong-age 1]
        [--compile-budget 1.25] [--skip-perf] [--json]

Order: hard gates (soundness ratchet, known-wrong ratchet, pass ratchet, perf
gate, compile-time budget), then primary weighted passes, then secondary census
progress. Perf is only a gate; it never adds score. Output follows the schema:
verdict PASS|FAIL plus findings, each with severity, area, expected, observed,
fix. Findings with severity "info" are report-only: they never fail the verdict
and never enter the oscillation ledger.

Census file: {"fences": [{"id": "PS1001:union", "blocks": 4}, ...]}. Progress is
the drop in total `blocks` weight (fences removed, weighted by packages blocked).
The oscillation ledger is a JSON list of {iteration, closed, opened}; a finding
that reappears after being closed ends the run.

The wrong tracker is a JSON object {"rows": {"<id>": {"seen": N, "issue":
"<url or null>"}}} aging every row that stays `wrong-output` across consecutive
verdicts. Past `--wrong-age` runs the row emits a finding: report-only once an
issue is recorded, P2 while it is still untracked. A tracked row that flips to
`pass` emits a closing finding and leaves the tracker.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from benchmarks.compat2.outcomes import SOUNDNESS
from benchmarks.compat2.score import weighted_pass

ROOT = Path(__file__).resolve().parents[2]


def _finding(severity: str, area: str, expected: str, observed: str, fix: str) -> dict[str, str]:
    return {"severity": severity, "area": area, "expected": expected, "observed": observed, "fix": fix}


def _outcomes(results: dict[str, Any]) -> dict[str, str]:
    return {r["id"]: r["outcome"] for r in results["rows"]}


def soundness_gate(base: dict[str, Any], cand: dict[str, Any]) -> list[dict[str, str]]:
    before = _outcomes(base)
    out = []
    for row in cand.get("rows", []):
        is_must_fence = row["id"].startswith("must_fence:") or row.get("area") == "must_fence"
        if is_must_fence:
            if row["outcome"] != "pass" and before.get(row["id"]) == "pass":
                out.append(
                    _finding(
                        "P0",
                        f"soundness:{row['id']}",
                        "construct must refuse with expected PS code (must-fence soundness ratchet)",
                        f"{row['id']} stopped refusing: {row.get('evidence', '')}",
                        "restore the refusal fence; dynamic constructs must not silently compile",
                    )
                )
            elif row["outcome"] in SOUNDNESS and before.get(row["id"]) not in SOUNDNESS:
                out.append(
                    _finding(
                        "P0",
                        f"soundness:{row['id']}",
                        "construct must refuse with expected PS code (must-fence soundness ratchet)",
                        f"{row['id']} is now {row['outcome']}: {row.get('evidence', '')}",
                        "restore the refusal fence; dynamic constructs must not silently compile",
                    )
                )
        elif row["outcome"] in SOUNDNESS and before.get(row["id"]) not in SOUNDNESS:
            out.append(
                _finding(
                    "P0",
                    f"soundness:{row['id']}",
                    "no new wrong-output or crash (plan A6 soundness ratchet)",
                    f"{row['id']} is now {row['outcome']}: {row['evidence']}",
                    "fix the lowering or fence the construct instead of miscompiling it",
                )
            )
    return out


def known_wrong_gate(
    base: dict[str, Any], cand: dict[str, Any], tracker: Path, *, min_age: int = 1
) -> list[dict[str, str]]:
    """Second arm of the soundness ratchet: a wrong-output row that stays wrong.

    The new-failure arm above cannot see a row that was already `wrong-output`
    in the baseline, so a known divergence would sit frozen forever. This arm
    ages each persistent row in `tracker`; past `min_age` runs it is a finding
    carrying the tracking-issue link (or demanding one). A tracked row that
    flips to `pass` emits a closing finding and leaves the tracker.
    """
    before = _outcomes(base)
    state: dict[str, Any] = json.loads(tracker.read_text(encoding="utf-8")) if tracker.exists() else {"rows": {}}
    rows: dict[str, dict[str, Any]] = state.setdefault("rows", {})
    persistent = {
        row["id"]
        for row in cand.get("rows", [])
        if row["outcome"] == "wrong-output" and before.get(row["id"]) == "wrong-output"
    }
    findings = []
    for rid in sorted(persistent):
        entry = rows.setdefault(rid, {"seen": 0, "issue": None})
        entry["seen"] += 1
        if entry["seen"] < min_age:
            continue
        if entry["issue"]:
            findings.append(
                _finding(
                    "info",
                    f"known-wrong:{rid}",
                    "a tracked wrong-output row keeps its issue link until it is fixed",
                    f"{rid} has been wrong-output for {entry['seen']} runs; tracked by {entry['issue']}",
                    f"close {entry['issue']} when the row flips to pass",
                )
            )
        else:
            findings.append(
                _finding(
                    "P2",
                    f"known-wrong:{rid}",
                    "a persistent wrong-output is a tracked divergence, not a frozen constant",
                    f"{rid} has been wrong-output for {entry['seen']} runs with no tracking issue",
                    "file a tracking issue and record it under this row in the wrong tracker",
                )
            )
    fixed = {
        row["id"]
        for row in cand.get("rows", [])
        if row["outcome"] == "pass" and before.get(row["id"]) == "wrong-output"
    }
    for rid in sorted(fixed | (set(rows) - persistent)):
        entry = rows.pop(rid, {"seen": 0, "issue": None})
        if rid not in fixed:
            continue
        findings.append(
            _finding(
                "info",
                f"fixed:{rid}",
                "a known wrong-output row stays wrong-output until the divergence is fixed",
                f"{rid} is now pass after {entry['seen']} tracked runs",
                f"close the tracking issue {entry['issue']}" if entry["issue"] else "divergence fixed",
            )
        )
    tracker.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return findings


def must_fence_gate(must_fence_data: dict[str, Any] | None) -> list[dict[str, str]]:
    """Hard gate for the must-fence adversarial corpus.

    A construct that stopped refusing is a P0 soundness failure.
    """
    if not must_fence_data:
        return []
    out = []
    for fail in must_fence_data.get("failures", []):
        case_id = fail.get("id", "unknown")
        evidence = fail.get("evidence", "")
        expected_code = fail.get("expected_code", fail.get("code", "fence"))
        out.append(
            _finding(
                "P0",
                f"soundness:must_fence:{case_id}",
                f"construct must refuse with {expected_code} (must-fence soundness ratchet)",
                f"{case_id} stopped refusing: {evidence}",
                "restore the refusal fence; dynamic constructs must not silently compile",
            )
        )
    return out


def pass_gate(base: dict[str, Any], cand: dict[str, Any]) -> list[dict[str, str]]:
    after = _outcomes(cand)
    out = []
    for row in base["rows"]:
        if row["outcome"] == "pass" and after.get(row["id"]) != "pass":
            out.append(
                _finding(
                    "P0",
                    f"pass-ratchet:{row['id']}",
                    "no previously passing case flips",
                    f"{row['id']} was pass, now {after.get(row['id'], 'missing')}",
                    "restore the behavior or revert the change",
                )
            )
    return out


def compile_gate(base: dict[str, Any], cand: dict[str, Any], budget: float) -> list[dict[str, str]]:
    old, new = base.get("compile_s", 0.0), cand.get("compile_s", 0.0)
    if old > 0 and new > old * budget:
        return [
            _finding(
                "P1",
                "compile-time",
                f"total compile time within {budget:.2f}x of baseline {old:.1f}s",
                f"{new:.1f}s ({new / old:.2f}x)",
                "remove the quadratic pass or cache the expensive analysis",
            )
        ]
    return []


def perf_gate(skip: bool) -> list[dict[str, str]]:
    if skip:
        return []
    proc = subprocess.run(
        [sys.executable, "-m", "benchmarks.baseline.run", "--check"], cwd=ROOT, capture_output=True, text=True
    )
    if proc.returncode == 0:
        return []
    tail = (proc.stderr or proc.stdout).strip().splitlines()
    return [
        _finding(
            "P1",
            "perf",
            "benchmarks.baseline.run --check stays within reference",
            "; ".join(line.strip() for line in tail[:4]) or f"exit {proc.returncode}",
            "recover the regressed workload before keeping the change",
        )
    ]


def census_progress(before: dict[str, Any] | None, after: dict[str, Any] | None) -> float:
    def total(census: dict[str, Any] | None) -> float:
        return sum(f.get("blocks", 1) for f in (census or {}).get("fences", []))

    return total(before) - total(after)


def update_ledger(path: Path, closed: list[str], opened: list[str]) -> list[dict[str, str]]:
    """Append this iteration. A finding closed earlier that reopens ends the run."""

    history: list[dict[str, Any]] = json.loads(path.read_text()) if path.exists() else []
    ever_closed = {f for entry in history for f in entry["closed"]}
    findings = [
        _finding(
            "P0",
            f"oscillation:{f}",
            "a finding marked fixed stays fixed",
            f"{f} reappeared after being closed",
            "stop retrying; escalate to the human",
        )
        for f in opened
        if f in ever_closed
    ]
    history.append({"iteration": len(history) + 1, "closed": closed, "opened": opened})
    path.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
    return findings


def verdict(
    base: dict[str, Any],
    cand: dict[str, Any],
    *,
    census_before: dict[str, Any] | None = None,
    census_after: dict[str, Any] | None = None,
    ledger: Path | None = None,
    budget: float = 1.25,
    skip_perf: bool = False,
    must_fence: dict[str, Any] | None = None,
    wrong_tracker: Path | None = None,
    wrong_age: int = 1,
) -> dict[str, Any]:
    must_fence_data = must_fence if must_fence is not None else cand.get("must_fence")
    findings = [
        *soundness_gate(base, cand),
        *must_fence_gate(must_fence_data),
        *(known_wrong_gate(base, cand, wrong_tracker, min_age=wrong_age) if wrong_tracker else []),
        *pass_gate(base, cand),
        *compile_gate(base, cand, budget),
        *perf_gate(skip_perf),
    ]
    gates_red = any(f["severity"] != "info" for f in findings)
    primary = weighted_pass(cand) - weighted_pass(base)
    secondary = census_progress(census_before, census_after)
    if not gates_red and primary <= 0 and secondary <= 0:
        findings.append(
            _finding(
                "P2",
                "progress",
                "weighted passes up, or census fences removed",
                f"primary {primary:+.1f}, census {secondary:+.1f}",
                "drop the change or declare it part of an epic",
            )
        )
    if ledger is not None:
        previous = json.loads(ledger.read_text()) if ledger.exists() else []
        last_open = set(previous[-1]["opened"]) if previous else set()
        now_open = {f["area"] for f in findings if f["severity"] != "info"}
        findings.extend(update_ledger(ledger, sorted(last_open - now_open), sorted(now_open)))
    return {
        "verdict": "FAIL" if any(f["severity"] != "info" for f in findings) else "PASS",
        "primary_delta": primary,
        "census_delta": secondary,
        "findings": findings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Keep/drop verdict for one iteration.")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--census-before")
    parser.add_argument("--census-after")
    parser.add_argument("--ledger", help="per-run anti-oscillation ledger JSON")
    parser.add_argument(
        "--wrong-tracker",
        help="known-wrong-output tracker JSON; ages persistent wrong-output rows into findings",
    )
    parser.add_argument(
        "--wrong-age",
        type=int,
        default=1,
        help="verdict runs a row may stay wrong-output before it is a finding (default 1)",
    )
    parser.add_argument("--must-fence", help="must-fence corpus results JSON")
    parser.add_argument("--compile-budget", type=float, default=1.25)
    parser.add_argument("--skip-perf", action="store_true")
    args = parser.parse_args(argv)

    def load(path: str | None) -> dict[str, Any] | None:
        return json.loads(Path(path).read_text()) if path else None

    result = verdict(
        load(args.baseline) or {},
        load(args.candidate) or {},
        census_before=load(args.census_before),
        census_after=load(args.census_after),
        ledger=Path(args.ledger) if args.ledger else None,
        budget=args.compile_budget,
        skip_perf=args.skip_perf,
        must_fence=load(args.must_fence),
        wrong_tracker=Path(args.wrong_tracker) if args.wrong_tracker else None,
        wrong_age=args.wrong_age,
    )
    print(json.dumps(result, indent=2))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
