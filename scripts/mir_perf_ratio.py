#!/usr/bin/env python3
"""Receipt-1: wall-time ratio of the direct emitter vs the opt-in MIR lane.

Compiles and times a fixed set of kernel/stdlib/scripting baseline workloads
on both lanes, then prints a markdown table and optional JSON. Median of N
runs (N configurable); noise is the interquartile spread and a bootstrap 95%
interval on the MIR/direct ratio. Report only: no CI gate, and this tool
does not write `benchmarks/baseline/reference.json`.

    uv run python scripts/mir_perf_ratio.py
    uv run python scripts/mir_perf_ratio.py --runs 3 --workloads sum-squares,horner
    uv run python scripts/mir_perf_ratio.py --json-out ratio.json --md-out ratio.md
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from benchmarks.harness import (  # noqa: E402
    bootstrap_ratio,
    geomean,
    literal_args,
    measure_command,
    median,
    verdict,
)

from pyscriptc.miremit import MIR_ENV  # noqa: E402

SUITE = ROOT / "benchmarks" / "baseline" / "suite.json"
CATALOG = ROOT / "benchmarks" / "runtime" / "workloads.json"
REFERENCE = ROOT / "benchmarks" / "baseline" / "reference.json"
LANE_GROUPS = ("kernel", "stdlib", "scripting")
DEFAULT_RUNS = 5


CompileFn = Callable[[Path, str, list[Any], Path, bool], subprocess.CompletedProcess[str]]
MeasureFn = Callable[[list[str]], Any]


def load_fixed_set(
    catalog_path: Path = CATALOG,
    suite_path: Path = SUITE,
    names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Workloads in the baseline suite whose group is kernel, stdlib, or scripting."""
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    by_name = {row["name"]: row for row in catalog["workloads"]}
    chosen = list(suite["workloads"]) if names is None else names
    missing = [name for name in chosen if name not in by_name]
    if missing:
        raise SystemExit(f"unknown workload: {', '.join(missing)}")
    rows: list[dict[str, Any]] = []
    for name in chosen:
        row = by_name[name]
        if row.get("expect") == "fence":
            continue
        if row.get("group") not in LANE_GROUPS:
            continue
        rows.append(row)
    return rows


def lane_env(mir: bool) -> dict[str, str]:
    env = os.environ.copy()
    env[MIR_ENV] = "1" if mir else "0"
    env["PYTHONHASHSEED"] = "0"
    return env


def compile_lane(
    source: Path,
    call: str,
    args: list[Any],
    output: Path,
    mir: bool,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "pyscriptc",
        "compile",
        str(source),
        "--emit",
        "bin",
        "--call",
        call,
        "--args",
        literal_args(args),
        "-o",
        str(output),
    ]
    return subprocess.run(command, capture_output=True, text=True, env=lane_env(mir))


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile of an empty sample")
    pos = q * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] * (1.0 - frac) + ordered[high] * frac


def spread(values: list[float], mid: float) -> dict[str, float]:
    p25 = _percentile(values, 0.25)
    p75 = _percentile(values, 0.75)
    return {
        "p25_ms": round(p25, 3),
        "p75_ms": round(p75, 3),
        "iqr_over_median": round((p75 - p25) / max(mid, 1e-9), 4),
    }


def _tail(text: str | None) -> str:
    if not text:
        return ""
    lines = text.strip().splitlines()
    return lines[-1] if lines else ""


def time_binary(binary: Path, runs: int, measure: MeasureFn) -> tuple[list[float], dict[str, float]]:
    samples: list[float] = []
    for _ in range(runs):
        sample = measure([str(binary)])
        if sample.returncode != 0:
            raise RuntimeError(_tail(sample.stderr) or f"run exit {sample.returncode}")
        samples.append(float(sample.elapsed_ms))
    mid = median(samples)
    return samples, spread(samples, mid)


def compare_workload(
    work: Path,
    workload: dict[str, Any],
    runs: int,
    compile_fn: CompileFn = compile_lane,
    measure: MeasureFn = measure_command,
) -> dict[str, Any]:
    source = ROOT / "benchmarks" / "runtime" / workload["entry"]
    row: dict[str, Any] = {
        "name": workload["name"],
        "group": workload.get("group", "kernel"),
        "call": workload["call"],
        "args": workload["args"],
        "status": "error",
        "samples": runs,
    }
    direct_bin = work / f"{workload['name']}-direct"
    mir_bin = work / f"{workload['name']}-mir"
    direct_cc = compile_fn(source, workload["call"], workload["args"], direct_bin, False)
    if direct_cc.returncode != 0:
        row["detail"] = _tail(direct_cc.stderr or direct_cc.stdout) or f"direct compile exit {direct_cc.returncode}"
        return row
    mir_cc = compile_fn(source, workload["call"], workload["args"], mir_bin, True)
    if mir_cc.returncode != 0:
        row["detail"] = _tail(mir_cc.stderr or mir_cc.stdout) or f"mir compile exit {mir_cc.returncode}"
        return row
    try:
        direct_ms, direct_spread = time_binary(direct_bin, runs, measure)
        mir_ms, mir_spread = time_binary(mir_bin, runs, measure)
    except RuntimeError as exc:
        row["detail"] = str(exc)
        return row
    direct_median = median(direct_ms)
    mir_median = median(mir_ms)
    ratio = mir_median / max(direct_median, 1e-9)
    low, high = bootstrap_ratio(mir_ms, direct_ms)
    row.update(
        {
            "status": "ok",
            "direct_median_ms": round(direct_median, 3),
            "mir_median_ms": round(mir_median, 3),
            "ratio": ratio,
            "direct_spread": direct_spread,
            "mir_spread": mir_spread,
            "ratio_ci95": {"low": round(low, 4), "high": round(high, 4)},
            "verdict": verdict(low, high),
        }
    )
    return row


def compare_lanes(
    workloads: list[dict[str, Any]],
    runs: int,
    compile_fn: CompileFn = compile_lane,
    measure: MeasureFn = measure_command,
    tmp: Path | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if tmp is not None:
        for workload in workloads:
            rows.append(compare_workload(tmp, workload, runs, compile_fn, measure))
    else:
        with tempfile.TemporaryDirectory(prefix="pyscriptc-mir-ratio-") as directory:
            work = Path(directory)
            for workload in workloads:
                rows.append(compare_workload(work, workload, runs, compile_fn, measure))
    ok = [row for row in rows if row["status"] == "ok"]
    report: dict[str, Any] = {
        "schema": 1,
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runs": runs,
        "ratio": "mir_median_ms / direct_median_ms",
        "groups": list(LANE_GROUPS),
        "workloads": rows,
    }
    if ok:
        report["geomean_ratio"] = geomean([row["ratio"] for row in ok])
        grouped: dict[str, list[float]] = {}
        for row in ok:
            grouped.setdefault(str(row["group"]), []).append(float(row["ratio"]))
        report["group_geomeans"] = {name: geomean(values) for name, values in grouped.items()}
    return report


def render_markdown(report: dict[str, Any]) -> str:
    """Markdown ratio table. Lower ratio means the MIR lane was faster."""
    lines = [
        "### Direct vs MIR lane (wall time)",
        "",
        f"median of {report['runs']} runs; ratio is MIR / direct (lower is faster on MIR). Report only.",
        "",
        "| workload | group | direct ms | MIR ms | ratio | CI95 | iqr/med direct | iqr/med MIR | verdict |",
        "|---|---|---:|---:|---:|---|---:|---:|---|",
    ]
    for row in report["workloads"]:
        if row["status"] != "ok":
            detail = str(row.get("detail", row["status"])).replace("|", " ")
            status = f"{row['status']}: {detail}"
            group = row.get("group", "")
            lines.append(f"| {row['name']} | {group} | - | - | - | - | - | - | {status} |")
            continue
        ci = row["ratio_ci95"]
        times = f"{row['direct_median_ms']:.3f} | {row['mir_median_ms']:.3f}"
        ratio = f"{row['ratio']:.4f} | {ci['low']:.3f}..{ci['high']:.3f}"
        noise = f"{row['direct_spread']['iqr_over_median']:.4f} | {row['mir_spread']['iqr_over_median']:.4f}"
        lines.append(f"| {row['name']} | {row['group']} | {times} | {ratio} | {noise} | {row['verdict']} |")
    geomean_ratio = report.get("geomean_ratio")
    if geomean_ratio is not None:
        lines.append("")
        lines.append(f"geomean ratio {geomean_ratio:.4f}.")
        groups = report.get("group_geomeans") or {}
        for name in LANE_GROUPS:
            if name in groups:
                lines.append(f"{name} geomean {groups[name]:.4f}.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Time kernel/stdlib/scripting workloads on both emit lanes; print a ratio table."
    )
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="samples per workload per lane")
    parser.add_argument("--workloads", help="comma-separated subset of the fixed set")
    parser.add_argument("--json-out", type=Path, help="write the JSON report here")
    parser.add_argument("--md-out", type=Path, help="write the markdown table here")
    parser.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    args = parser.parse_args(argv)
    if args.runs < 1:
        print("--runs must be at least 1", file=sys.stderr)
        return 2
    names = None
    if args.workloads:
        names = [name.strip() for name in args.workloads.split(",") if name.strip()]
    workloads = load_fixed_set(names=names)
    if not workloads:
        print("no kernel/stdlib/scripting workloads in the selection", file=sys.stderr)
        return 2
    if not REFERENCE.is_file():
        print(f"baseline reference missing (not written by this tool): {REFERENCE}", file=sys.stderr)
    report = compare_lanes(workloads, args.runs)
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
