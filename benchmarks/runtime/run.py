#!/usr/bin/env python3
"""Time gates-v1 workloads against CPython.

Mirrors the process half of scriptc's benchmarks/runtime runner: compile,
require stdout to match the baseline, then time. The baseline here is
CPython, which is the island. scriptc's baseline for the same folder is Node.

  uv run python -m benchmarks.runtime.run [--runs=5] [--workloads=a,b] [--json=out.json]

A workload with "expect": "fence" is checked, not timed. Anything that
diverges from CPython is reported and left out of the geomean.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from benchmarks.harness import (
    bootstrap_ratio,
    compile_call,
    cpython_stdout,
    geomean,
    median,
    oracle_command,
    time_command,
    verdict,
    versus_cpython,
)

HERE = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Time pyscriptc workloads against CPython.")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--workloads", help="comma-separated workload names")
    parser.add_argument("--json", dest="json_path", help="write the report to this path")
    args = parser.parse_args(argv)
    if args.runs < 1:
        print("--runs must be at least 1", file=sys.stderr)
        return 2

    catalog = json.loads((HERE / "workloads.json").read_text(encoding="utf-8"))
    selected = _select(catalog["workloads"], args.workloads)
    rows: list[dict[str, Any]] = []
    failed = False
    with tempfile.TemporaryDirectory(prefix="pyscriptc-bench-") as tmp:
        work = Path(tmp)
        for workload in selected:
            row = _run_workload(work, workload, args.runs)
            rows.append(row)
            _print_row(row)
            if row["status"] not in {"ok", "fence"}:
                failed = True
            if row["status"] == "fence" and workload.get("expect") != "fence":
                failed = True

    ratios = [row["ratio"] for row in rows if row["status"] == "ok"]
    report: dict[str, Any] = {
        "baseline": "cpython",
        "reference": catalog["reference"],
        "runs": args.runs,
        "workloads": rows,
    }
    if ratios:
        report["geomean_vs_cpython"] = geomean(ratios)
        print(f"geomean {versus_cpython(report['geomean_vs_cpython'])}")
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 1 if failed else 0


def _select(workloads: list[dict[str, Any]], names: str | None) -> list[dict[str, Any]]:
    if not names:
        return workloads
    wanted = {name.strip() for name in names.split(",") if name.strip()}
    chosen = [workload for workload in workloads if workload["name"] in wanted]
    missing = wanted - {workload["name"] for workload in chosen}
    if missing:
        raise SystemExit(f"unknown workload: {', '.join(sorted(missing))}")
    return chosen


def _run_workload(work: Path, workload: dict[str, Any], runs: int) -> dict[str, Any]:
    source = HERE / workload["entry"]
    binary = work / workload["name"]
    compiled = compile_call(source, workload["call"], workload["args"], binary)
    row: dict[str, Any] = {
        "name": workload["name"],
        "category": workload["category"],
        "scriptc": workload.get("scriptc"),
        "status": "error",
    }
    if workload.get("expect") == "fence":
        row["expect"] = "fence"
        if compiled.returncode == 3:
            row["status"] = "fence"
            return row
        row["detail"] = _tail(compiled.stderr or compiled.stdout)
        return row
    if compiled.returncode == 3:
        row["status"] = "fence"
        row["detail"] = _tail(compiled.stderr)
        return row
    if compiled.returncode != 0:
        row["detail"] = _tail(compiled.stderr or compiled.stdout)
        return row

    oracle = cpython_stdout(source, workload["call"], workload["args"])
    if oracle.returncode != 0:
        row["status"] = "cpython-error"
        row["detail"] = _tail(oracle.stderr)
        return row
    native_command = [str(binary)]
    cpython_command = oracle_command(source, workload["call"], workload["args"])
    _elapsed, probe = time_command(native_command)
    if probe.returncode != 0 or probe.stdout != oracle.stdout:
        row["status"] = "mismatch" if probe.returncode == 0 else "native-error"
        row["native_stdout"] = probe.stdout
        row["cpython_stdout"] = oracle.stdout
        row["detail"] = _tail(probe.stderr)
        return row
    time_command(native_command)
    time_command(cpython_command)

    native_samples: list[float] = []
    cpython_samples: list[float] = []
    for _ in range(runs):
        native_ms, native = time_command(native_command)
        cpython_ms, cpython = time_command(cpython_command)
        if native.returncode != 0 or native.stdout != oracle.stdout:
            row["status"] = "mismatch" if native.returncode == 0 else "native-error"
            return row
        if cpython.returncode != 0 or cpython.stdout != oracle.stdout:
            row["status"] = "cpython-error"
            return row
        native_samples.append(native_ms)
        cpython_samples.append(cpython_ms)

    low, high = bootstrap_ratio(native_samples, cpython_samples)
    native_median = median(native_samples)
    cpython_median = median(cpython_samples)
    ratio = native_median / max(cpython_median, 1e-9)
    row.update(
        {
            "status": "ok",
            "median_ms": round(native_median, 3),
            "cpython_ms": round(cpython_median, 3),
            "ratio": ratio,
            "ci_low": low,
            "ci_high": high,
            "verdict": verdict(low, high),
            "samples": len(native_samples),
        }
    )
    return row


def _print_row(row: dict[str, Any]) -> None:
    if row["status"] == "ok":
        print(
            f"{row['name']:<16} {row['median_ms']:>8.2f} ms  "
            f"cpython {row['cpython_ms']:>8.2f} ms  "
            f"{versus_cpython(row['ratio'])}  {row['verdict']}  "
            f"ci [{_speed_bound(row['ci_high'])}, {_speed_bound(row['ci_low'])}]"
        )
        return
    detail = f"  {row['detail']}" if row.get("detail") else ""
    print(f"{row['name']:<16} {row['status']}{detail}")


def _speed_bound(ratio: float) -> str:
    if ratio <= 0:
        return "infx"
    return f"{1.0 / ratio:.1f}x"


def _tail(text: str) -> str:
    text = text.strip()
    if len(text) <= 400:
        return text
    return text[-400:]


if __name__ == "__main__":
    raise SystemExit(main())
