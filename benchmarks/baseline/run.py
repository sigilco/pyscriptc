#!/usr/bin/env python3
"""Record or check the pyscriptc baseline.

Each lowered workload is compiled, checked against CPython's stdout, then
sampled for wall time and peak RSS. ``--check`` compares pyscriptc/CPython
ratios with the reference file for this host.

``reference.json`` is the Linux reference already in the tree. A later Mac
records ``reference-macos-arm64.json`` instead: on Darwin arm64, ``--record``
and ``--check`` default to that file, and any other host refuses to write it.
The macOS file is produced only by ``--record`` on macOS arm64. This tree
does not ship macOS millisecond or RSS numbers.

  uv run python -m benchmarks.baseline.run --check
  uv run python -m benchmarks.baseline.run --record
  uv run python -m benchmarks.baseline.run --runs=1 --workloads=sum-squares --json=out.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.baseline.compare import compare_reports
from benchmarks.harness import (
    compile_call,
    cpython_stdout,
    geomean,
    measure_command,
    median,
    oracle_command,
    repo_root,
    versus_cpython,
)

HERE = Path(__file__).resolve().parent
CATALOG = repo_root() / "benchmarks" / "runtime" / "workloads.json"
REFERENCE = HERE / "reference.json"
MACOS_ARM64_REFERENCE = HERE / "reference-macos-arm64.json"


def reference_for_host(system: str | None = None, machine: str | None = None) -> Path:
    """Linux keeps ``reference.json``. macOS arm64 uses its own file."""

    system = platform.system() if system is None else system
    machine = platform.machine() if machine is None else machine
    if system == "Darwin" and machine == "arm64":
        return MACOS_ARM64_REFERENCE
    return REFERENCE


def _is_macos_arm64_slot(path: str) -> bool:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve() == MACOS_ARM64_REFERENCE.resolve()


def refuses_macos_record(path: str, system: str | None = None, machine: str | None = None) -> bool:
    """The macOS slot is written only by ``--record`` on Darwin arm64."""

    system = platform.system() if system is None else system
    machine = platform.machine() if machine is None else machine
    if not _is_macos_arm64_slot(path):
        return False
    return not (system == "Darwin" and machine == "arm64")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Baseline time and RSS for pyscriptc.")
    parser.add_argument("--runs", type=int)
    parser.add_argument("--workloads", help="comma-separated workload names")
    parser.add_argument("--json", dest="json_path", help="write this run to a path")
    host_reference = reference_for_host()
    parser.add_argument(
        "--record",
        nargs="?",
        const=str(host_reference),
        help=(
            "write this run as the reference metric set for this host "
            f"({host_reference.name}). Linux uses reference.json. "
            "macOS arm64 uses reference-macos-arm64.json."
        ),
    )
    parser.add_argument(
        "--check",
        nargs="?",
        const=str(host_reference),
        help=(
            "compare pyscriptc/CPython ratios with the reference metric set for this host "
            f"({host_reference.name}). Pass a path to check another file."
        ),
    )
    args = parser.parse_args(argv)
    if args.record and refuses_macos_record(args.record):
        print(
            "reference-macos-arm64.json is produced only by --record on macOS arm64",
            file=sys.stderr,
        )
        return 2

    suite = json.loads((HERE / "suite.json").read_text(encoding="utf-8"))
    runs = args.runs if args.runs is not None else int(suite["runs"])
    if runs < 1:
        print("--runs must be at least 1", file=sys.stderr)
        return 2
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    selected_names = _names(args.workloads, suite["workloads"])
    workloads = _select(catalog["workloads"], selected_names)

    rows: list[dict[str, Any]] = []
    failed = False
    with tempfile.TemporaryDirectory(prefix="pyscriptc-baseline-") as tmp:
        work = Path(tmp)
        for workload in workloads:
            row = _run_workload(work, workload, runs)
            rows.append(row)
            _print_row(row)
            if row["status"] != "ok":
                failed = True

    report = _report(rows, runs, selected_names if args.workloads else None)
    _print_geomean(report)
    if args.json_path:
        _write(Path(args.json_path), report, role="run")
    if args.record:
        _write(Path(args.record), report, role="reference")
        print(f"recorded {args.record}")
    if args.check:
        reference = json.loads(Path(args.check).read_text(encoding="utf-8"))
        failures = compare_reports(report, reference)
        if failures:
            print("reference check failed:", file=sys.stderr)
            for failure in failures:
                print(f"  {failure}", file=sys.stderr)
            return 1
        print(f"within reference {args.check}")
    return 1 if failed else 0


def _names(raw: str | None, default: list[str]) -> list[str]:
    if not raw:
        return list(default)
    return [name.strip() for name in raw.split(",") if name.strip()]


def _select(catalog: list[dict[str, Any]], names: list[str]) -> list[dict[str, Any]]:
    by_name = {workload["name"]: workload for workload in catalog}
    missing = [name for name in names if name not in by_name]
    if missing:
        raise SystemExit(f"unknown workload: {', '.join(missing)}")
    fenced = [name for name in names if by_name[name].get("expect") == "fence"]
    if fenced:
        raise SystemExit(f"baseline does not time fences: {', '.join(fenced)}")
    return [by_name[name] for name in names]


def _run_workload(work: Path, workload: dict[str, Any], runs: int) -> dict[str, Any]:
    source = repo_root() / "benchmarks" / "runtime" / workload["entry"]
    binary = work / workload["name"]
    started = time.perf_counter()
    compiled = compile_call(source, workload["call"], workload["args"], binary)
    compile_ms = (time.perf_counter() - started) * 1000.0
    row: dict[str, Any] = {
        "name": workload["name"],
        "call": workload["call"],
        "args": workload["args"],
        "group": workload.get("group", "kernel"),
        "module": workload.get("module", ""),
        "status": "error",
        "compile_ms": round(compile_ms, 3),
    }
    if compiled.returncode != 0:
        row["detail"] = _tail(compiled.stderr or compiled.stdout) or f"compile exit {compiled.returncode}"
        return row

    oracle = cpython_stdout(source, workload["call"], workload["args"])
    if oracle.returncode != 0:
        row["status"] = "cpython-error"
        row["detail"] = _tail(oracle.stderr)
        return row
    native_command = [str(binary)]
    cpython_command = oracle_command(source, workload["call"], workload["args"])
    probe = measure_command(native_command)
    if probe.returncode != 0 or probe.stdout != oracle.stdout:
        row["status"] = "mismatch" if probe.returncode == 0 else "native-error"
        row["detail"] = _tail(probe.stderr)
        return row
    measure_command(cpython_command)

    native_ms: list[float] = []
    native_rss: list[float] = []
    cpython_ms: list[float] = []
    cpython_rss: list[float] = []
    for _ in range(runs):
        native = measure_command(native_command)
        cpython = measure_command(cpython_command)
        if native.returncode != 0 or native.stdout != oracle.stdout:
            row["status"] = "mismatch" if native.returncode == 0 else "native-error"
            return row
        if cpython.returncode != 0 or cpython.stdout != oracle.stdout:
            row["status"] = "cpython-error"
            return row
        native_ms.append(native.elapsed_ms)
        native_rss.append(float(native.rss_kb))
        cpython_ms.append(cpython.elapsed_ms)
        cpython_rss.append(float(cpython.rss_kb))

    native_median_ms = median(native_ms)
    cpython_median_ms = median(cpython_ms)
    native_rss_kb = median(native_rss)
    cpython_rss_kb = median(cpython_rss)
    row.update(
        {
            "status": "ok",
            "native_median_ms": round(native_median_ms, 3),
            "cpython_median_ms": round(cpython_median_ms, 3),
            "time_ratio": native_median_ms / max(cpython_median_ms, 1e-9),
            "native_rss_kb": round(native_rss_kb),
            "cpython_rss_kb": round(cpython_rss_kb),
            "rss_ratio": native_rss_kb / max(cpython_rss_kb, 1.0),
            "binary_bytes": binary.stat().st_size,
            "samples": runs,
        }
    )
    return row


def _report(rows: list[dict[str, Any]], runs: int, selected: list[str] | None) -> dict[str, Any]:
    ok = [row for row in rows if row["status"] == "ok"]
    report: dict[str, Any] = {
        "schema": 1,
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cpython": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "metrics": ["wall_ms", "rss_kb"],
        "runs": runs,
        "workloads": rows,
    }
    if selected is not None:
        report["selected"] = selected
    kernel = [row for row in ok if row.get("group", "kernel") == "kernel"]
    basis = kernel if kernel else ok
    if basis:
        report["geomean_group"] = "kernel" if kernel else "selected"
        report["geomean_time_ratio"] = geomean([row["time_ratio"] for row in basis])
        report["geomean_rss_ratio"] = geomean([row["rss_ratio"] for row in basis])
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in ok:
        group = str(row.get("group", "kernel"))
        if group == "kernel":
            continue
        grouped.setdefault(group, []).append(row)
    if grouped:
        report["group_geomeans"] = {
            name: {
                "geomean_time_ratio": geomean([item["time_ratio"] for item in items]),
                "geomean_rss_ratio": geomean([item["rss_ratio"] for item in items]),
            }
            for name, items in grouped.items()
        }
    return report


def _write(path: Path, report: dict[str, Any], *, role: str) -> None:
    body = dict(report)
    body["role"] = role
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def _print_row(row: dict[str, Any]) -> None:
    if row["status"] != "ok":
        detail = f"  {row['detail']}" if row.get("detail") else ""
        print(f"{row['name']:<18} {row['status']}{detail}")
        return
    print(
        f"{row['name']:<18} "
        f"{row['native_median_ms']:>8.2f} ms  cpython {row['cpython_median_ms']:>8.2f} ms  "
        f"time {versus_cpython(row['time_ratio'])}  "
        f"rss {row['native_rss_kb']:>6} / {row['cpython_rss_kb']:<6} KB  "
        f"rss {versus_cpython(row['rss_ratio'])}"
    )


def _print_geomean(report: dict[str, Any]) -> None:
    if "geomean_time_ratio" in report:
        label = "kernel geomean" if report.get("geomean_group") == "kernel" else "geomean"
        print(
            f"{label}  "
            f"time {versus_cpython(report['geomean_time_ratio'])}  "
            f"rss {versus_cpython(report['geomean_rss_ratio'])}"
        )
    for name, group in (report.get("group_geomeans") or {}).items():
        print(
            f"{name} geomean  "
            f"time {versus_cpython(group['geomean_time_ratio'])}  "
            f"rss {versus_cpython(group['geomean_rss_ratio'])}"
        )


def _tail(text: str) -> str:
    text = text.strip()
    if len(text) <= 400:
        return text
    return text[-400:]


if __name__ == "__main__":
    raise SystemExit(main())
