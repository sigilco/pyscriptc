#!/usr/bin/env python3
"""Receipt-2: outcome flips between two compat2 scorer result files.

Takes two `score.py --out` JSON documents and reports every row whose
outcome changed, in both directions (A to B and B to A). This is the
v0.8.0 receipt shape: pass counts, weighted pass, and "N outcome flips
in either direction". Report only: no CI gate.

The tool reads the two result files and nothing else. It does not load
the probe corpus, the scorer, or any closed split.

    uv run python scripts/compat_no_flip.py before.json after.json
    uv run python scripts/compat_no_flip.py before.json after.json --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_results(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        raise ValueError(f"{path}: not a compat2 score file (no rows list)")
    for row in data["rows"]:
        if not isinstance(row, dict) or "id" not in row or "outcome" not in row:
            raise ValueError(f"{path}: a row is missing id or outcome")
    return data


def _outcomes(results: dict[str, Any]) -> dict[str, str]:
    return {str(row["id"]): str(row["outcome"]) for row in results["rows"]}


def _weight(results: dict[str, Any], probe_id: str) -> float:
    weights = results.get("weights") or {}
    if isinstance(weights, dict) and probe_id in weights:
        return float(weights[probe_id])
    return 1.0


def pass_count(results: dict[str, Any]) -> int:
    return sum(1 for row in results["rows"] if row["outcome"] == "pass")


def weighted_pass(results: dict[str, Any]) -> float:
    return sum(_weight(results, str(row["id"])) for row in results["rows"] if row["outcome"] == "pass")


def weighted_total(results: dict[str, Any]) -> float:
    return sum(_weight(results, str(row["id"])) for row in results["rows"])


def compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Flips in both directions, keyed by probe id."""
    left = _outcomes(before)
    right = _outcomes(after)
    ids = sorted(set(left) | set(right))
    a_to_b: list[dict[str, str]] = []
    b_to_a: list[dict[str, str]] = []
    missing_after: list[str] = []
    missing_before: list[str] = []
    for probe_id in ids:
        if probe_id not in left:
            missing_before.append(probe_id)
            continue
        if probe_id not in right:
            missing_after.append(probe_id)
            continue
        if left[probe_id] == right[probe_id]:
            continue
        a_to_b.append({"id": probe_id, "from": left[probe_id], "to": right[probe_id]})
        b_to_a.append({"id": probe_id, "from": right[probe_id], "to": left[probe_id]})
    before_pass = pass_count(before)
    after_pass = pass_count(after)
    before_w = weighted_pass(before)
    after_w = weighted_pass(after)
    before_tot = weighted_total(before)
    after_tot = weighted_total(after)
    pct = (100.0 * before_w / before_tot) if before_tot else 0.0
    return {
        "schema": 1,
        "probes_before": len(before["rows"]),
        "probes_after": len(after["rows"]),
        "oracle_before": before.get("oracle"),
        "oracle_after": after.get("oracle"),
        "pass_before": before_pass,
        "pass_after": after_pass,
        "weighted_pass_before": before_w,
        "weighted_pass_after": after_w,
        "weighted_total_before": before_tot,
        "weighted_total_after": after_tot,
        "weighted_pass_pct_before": pct,
        "flips_either_direction": len(a_to_b),
        "a_to_b": a_to_b,
        "b_to_a": b_to_a,
        "missing_after": missing_after,
        "missing_before": missing_before,
    }


def receipt_line(report: dict[str, Any]) -> str:
    """The v0.8.0 one-line receipt."""
    n = report["probes_before"]
    oracle = report.get("oracle_before") or report.get("oracle_after") or "unknown"
    same_pass = report["pass_before"] == report["pass_after"]
    same_w = report["weighted_pass_before"] == report["weighted_pass_after"]
    both = "on both files" if same_pass and same_w else "before vs after"
    w = report["weighted_pass_before"]
    tot = report["weighted_total_before"]
    pct = (100.0 * w / tot) if tot else 0.0
    flips = report["flips_either_direction"]
    if same_pass and same_w:
        pass_bit = f"pass {report['pass_before']} and weighted pass {w:.1f} of {tot:.1f} ({pct:.2f}%) {both}"
    else:
        pass_bit = (
            f"pass {report['pass_before']} vs {report['pass_after']}, "
            f"weighted pass {w:.1f} of {tot:.1f} vs "
            f"{report['weighted_pass_after']:.1f} of {report['weighted_total_after']:.1f}"
        )
    return (
        f"Compat (scorer v2, {n} probes, oracle {oracle}): {pass_bit}, with {flips} outcome flips in either direction."
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "### Compat outcome flips",
        "",
        receipt_line(report),
        "",
        f"A to B: {len(report['a_to_b'])}. B to A: {len(report['b_to_a'])}.",
        "",
        "| id | A | B |",
        "|---|---|---|",
    ]
    for row in report["a_to_b"]:
        lines.append(f"| {row['id']} | {row['from']} | {row['to']} |")
    if not report["a_to_b"]:
        lines.append("| (none) | - | - |")
    if report["missing_after"] or report["missing_before"]:
        lines.append("")
        if report["missing_after"]:
            lines.append(f"missing in B: {len(report['missing_after'])}")
        if report["missing_before"]:
            lines.append(f"missing in A: {len(report['missing_before'])}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report outcome flips between two compat2 scorer result files (both directions)."
    )
    parser.add_argument("before", type=Path, help="baseline score JSON (A)")
    parser.add_argument("after", type=Path, help="candidate score JSON (B)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    parser.add_argument("--json-out", type=Path, help="write JSON here")
    parser.add_argument("--md-out", type=Path, help="write markdown here")
    args = parser.parse_args(argv)
    try:
        before = load_results(args.before)
        after = load_results(args.after)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    report = compare(before, after)
    table = render_markdown(report)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.md_out is not None:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(table, encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        sys.stdout.write(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
