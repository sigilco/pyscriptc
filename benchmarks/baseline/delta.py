#!/usr/bin/env python3
"""Compare two baseline bench JSON files and print a markdown delta table.

Report-only: the exit code never depends on the numbers. The workflow run
summary pairs the new bench JSON with the previous tag's file, and the same
helper drives a manual comparison between any two recorded runs.

Columns per workload: the median wall time and peak RSS of each run, the
ratio of the two medians, and each run's interquartile spread, so a
difference that sits inside the noise is visible as such. Rows whose samples
did not record a spread (older schema 1 reports) print the spread column
empty rather than failing.

    uv run python -m benchmarks.baseline.delta base.json head.json
    uv run python -m benchmarks.baseline.delta base.json head.json --summary "$GITHUB_STEP_SUMMARY"

Geomeans are compared per group and overall when both files carry them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_report(path: str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("workloads"), list):
        raise ValueError(f"{path}: not a baseline report (no workloads list)")
    return data


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def delta_table(base: dict[str, Any], head: dict[str, Any]) -> str:
    """Markdown table over the workloads both reports share, plus geomean rows."""
    base_rows = {row["name"]: row for row in base["workloads"] if isinstance(row, dict) and "name" in row}
    head_rows = {row["name"]: row for row in head["workloads"] if isinstance(row, dict) and "name" in row}
    lines = [
        "### Benchmarks: delta against the previous run",
        "",
        f"base `{base.get('recorded_at', '?')}`, head `{head.get('recorded_at', '?')}`. Report-only.",
        "",
        "| workload | base ms | head ms | time | base rss KB | head rss KB | rss | base iqr/med | head iqr/med |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name in sorted(set(base_rows) | set(head_rows)):
        b = base_rows.get(name)
        h = head_rows.get(name)
        if b is None:
            lines.append(f"| {name} | - | - | new in head | - | - | - | - | - |")
            continue
        if h is None:
            lines.append(f"| {name} | {b.get('native_median_ms', '?')} | - | dropped in head | - | - | - | - | - |")
            continue
        if b.get("status") != "ok" or h.get("status") != "ok":
            lines.append(f"| {name} | - | - | not ok ({b.get('status')}/{h.get('status')}) | - | - | - | - | - |")
            continue
        lines.append(_row(name, b, h))
    lines.append("")
    geomeans = _geomean_lines(base, head)
    if geomeans:
        lines += ["| workload | base | head | delta | | | | | |", "|---|---|---|---|---|---|---|---|---|"]
        lines += geomeans
        lines.append("")
    return "\n".join(lines)


def _row(name: str, b: dict[str, Any], h: dict[str, Any]) -> str:
    base_ms = float(b["native_median_ms"])
    head_ms = float(h["native_median_ms"])
    base_rss = _num(b.get("native_rss_kb"))
    head_rss = _num(h.get("native_rss_kb"))
    b_spread = b.get("native_ms_spread") or {}
    h_spread = h.get("native_ms_spread") or {}
    return (
        f"| {name} | {base_ms:.3f} | {head_ms:.3f} | {ratio_text(base_ms, head_ms)} "
        f"| {_rss_kb(base_rss)} | {_rss_kb(head_rss)} "
        f"| {_rss_text(base_rss, head_rss)} "
        f"| {_spread_text(b_spread)} | {_spread_text(h_spread)} |"
    )


def _spread_text(spread: dict[str, Any]) -> str:
    value = _num(spread.get("iqr_over_median"))
    return f"{value:.4f}" if value is not None else ""


def _rss_kb(value: float | None) -> str:
    """Peak RSS in KB. The runner records it as an integer, so print it as one."""
    return "?" if value is None else f"{value:.0f}"


def _rss_text(base_rss: float | None, head_rss: float | None) -> str:
    if base_rss is None or head_rss is None or base_rss <= 0 or head_rss <= 0:
        return "?"
    return ratio_text(base_rss, head_rss)


def ratio_text(base: float, head: float) -> str:
    if base <= 0:
        return "?"
    change = head / base
    direction = "faster" if change < 1 else "slower" if change > 1 else "same"
    return f"{change:.3f}x {direction}"


def _geomean_lines(base: dict[str, Any], head: dict[str, Any]) -> list[str]:
    """Time-geomean rows (pyscriptc/CPython ratio, lower is better) for shared groups."""
    lines: list[str] = []
    shared_groups = sorted(set(base.get("group_geomeans") or {}) & set(head.get("group_geomeans") or {}))
    if "geomean_time_ratio" in base and "geomean_time_ratio" in head:
        lines.append(
            f"| geomean ({base.get('geomean_group', 'kernel')}) "
            f"| {base['geomean_time_ratio']:.4f} | {head['geomean_time_ratio']:.4f} "
            f"| {ratio_text(float(base['geomean_time_ratio']), float(head['geomean_time_ratio']))} | | | | | |"
        )
    for group in shared_groups:
        b = float(base["group_geomeans"][group]["geomean_time_ratio"])
        h = float(head["group_geomeans"][group]["geomean_time_ratio"])
        lines.append(f"| geomean ({group}) | {b:.4f} | {h:.4f} | {ratio_text(b, h)} | | | | | |")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", help="the reference run, usually the previous tag's bench JSON")
    parser.add_argument("head", help="the new run to compare against it")
    parser.add_argument("--summary", help="also write the table to this file (GITHUB_STEP_SUMMARY)")
    args = parser.parse_args(argv)
    try:
        table = delta_table(load_report(args.base), load_report(args.head))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"delta: {exc}", file=sys.stderr)
        return 2
    print(table)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write(table + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
