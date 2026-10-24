"""Must-fence adversarial corpus runner and evaluator.

Loads probe cases from benchmarks/compat2/must_fence/*.jsonl, asserts that
each construct refuses with its expected diagnostic code across tiers T0-T3
(or compiles cleanly for desugar/island lanes), and provides structured results
for the verdict soundness ratchet.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
MUST_FENCE_DIR = Path(__file__).resolve().parent

PS_CODE = re.compile(r"PS\d{4}")


def load_cases(directory: Path | None = None) -> list[dict[str, Any]]:
    """Load all probe cases from JSONL files in directory."""
    dir_path = directory or MUST_FENCE_DIR
    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for path in sorted(dir_path.glob("*.jsonl")):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
            case_id = item["id"]
            if case_id in seen_ids:
                raise ValueError(f"{path}:{line_no}: duplicate case id {case_id!r}")
            seen_ids.add(case_id)
            cases.append(item)
    return cases


def _tail(text: str) -> str:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def evaluate_case(case: dict[str, Any], timeout: float = 60.0) -> dict[str, Any]:
    """Compile one must-fence case and evaluate whether it behaved as asserted.

    A construct that is asserted to refuse must produce a non-zero exit code (typically 3)
    and output the expected PSxxxx code in stderr. Silently compiling (exit 0) is a P0
    soundness failure.
    """
    expected_outcome = case.get("expected", "fence")
    expected_code = case.get("code", "")
    extra_args = list(case.get("args", []))
    source = case["source"]

    with tempfile.TemporaryDirectory(prefix="must-fence-") as tmpdir:
        tmp_path = Path(tmpdir) / "case.py"
        tmp_path.write_text(source, encoding="utf-8")
        out_bin = Path(tmpdir) / "out"

        cmd = [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(tmp_path),
            "--skip-check",
            *extra_args,
        ]
        if "--emit" not in extra_args and "-o" not in extra_args and not case.get("no_output_flag"):
            cmd.extend(["-o", str(out_bin)])

        started = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmpdir,
            )
            spent = round(time.monotonic() - started, 3)
        except subprocess.TimeoutExpired:
            return {
                "id": case["id"],
                "tier": case.get("tier", "T0"),
                "construct": case.get("construct", ""),
                "expected": expected_outcome,
                "expected_code": expected_code,
                "outcome": "timeout",
                "held": False,
                "evidence": f"compile timed out after {timeout}s",
                "compile_s": timeout,
            }

    stderr_tail = _tail(proc.stderr) or _tail(proc.stdout)
    found_codes = PS_CODE.findall(proc.stderr)

    if expected_outcome == "fence":
        if proc.returncode == 0:
            return {
                "id": case["id"],
                "tier": case.get("tier", "T0"),
                "construct": case.get("construct", ""),
                "expected": "fence",
                "expected_code": expected_code,
                "outcome": "wrong-output",
                "held": False,
                "evidence": f"silently compiled (rc=0), expected refusal {expected_code}",
                "compile_s": spent,
            }
        if expected_code and expected_code in proc.stderr:
            return {
                "id": case["id"],
                "tier": case.get("tier", "T0"),
                "construct": case.get("construct", ""),
                "expected": "fence",
                "expected_code": expected_code,
                "outcome": "pass",
                "held": True,
                "evidence": f"refused with {expected_code}",
                "compile_s": spent,
            }
        # Refused, but with a different code than asserted
        observed = ",".join(found_codes) if found_codes else f"rc={proc.returncode}"
        return {
            "id": case["id"],
            "tier": case.get("tier", "T0"),
            "construct": case.get("construct", ""),
            "expected": "fence",
            "expected_code": expected_code,
            "outcome": "wrong-output",
            "held": False,
            "evidence": f"refused with {observed} instead of {expected_code}: {stderr_tail}",
            "compile_s": spent,
        }

    # expected_outcome == "pass" (e.g. T2 desugar, T1 island routing)
    if proc.returncode == 0:
        return {
            "id": case["id"],
            "tier": case.get("tier", "T0"),
            "construct": case.get("construct", ""),
            "expected": "pass",
            "expected_code": "",
            "outcome": "pass",
            "held": True,
            "evidence": "compiled cleanly as expected",
            "compile_s": spent,
        }
    return {
        "id": case["id"],
        "tier": case.get("tier", "T0"),
        "construct": case.get("construct", ""),
        "expected": "pass",
        "expected_code": "",
        "outcome": "fence",
        "held": False,
        "evidence": f"failed to compile (rc={proc.returncode}): {stderr_tail}",
        "compile_s": spent,
    }


def run_must_fence(cases: list[dict[str, Any]] | None = None, timeout: float = 60.0) -> dict[str, Any]:
    """Run all must-fence probes and return a summary dictionary."""
    cases = cases if cases is not None else load_cases()
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for case in cases:
        res = evaluate_case(case, timeout=timeout)
        results.append(res)
        if not res["held"]:
            failures.append(res)

    rows = [
        {
            "id": f"must_fence:{r['id']}",
            "area": "must_fence",
            "category": "language",
            "outcome": r["outcome"],
            "code": r.get("expected_code", ""),
            "evidence": r["evidence"],
        }
        for r in results
    ]

    return {
        "passed": len(results) - len(failures),
        "failed": len(failures),
        "total": len(results),
        "failures": failures,
        "results": results,
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run must-fence adversarial probe corpus.")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-probe timeout in seconds")
    args = parser.parse_args(argv)

    summary = run_must_fence(timeout=args.timeout)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Must-fence corpus: {summary['passed']}/{summary['total']} passed, {summary['failed']} failed")
        if summary["failures"]:
            print("\nFailures:")
            for f in summary["failures"]:
                print(f"  [{f['tier']}] {f['id']}: {f['evidence']}")
    return 1 if summary["failed"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
