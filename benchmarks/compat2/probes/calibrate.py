"""Calibrate the generated probes against the current compiler (Linux only).

    uv run python -m benchmarks.compat2.probes.calibrate [--per-cell 12] [--workers 2] [--out calibration.json]

Samples every (area, category, kind) cell deterministically, plus every probe whose v1 row was `lowered` in the
latest frozen iteration. Each probe is wrapped with a zero-argument entry that passes the recorded args, compiled
with `pyscriptc compile --run --skip-check --call`, and the stdout is compared to the CPython oracle.
Exit code 3 from the compiler is a fence. Raises probes are only classified fence or compiled, because the
native exit status carries no exception name.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import engine

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FILES = ("builtins", "surface", "modules", "language", "v1equiv")
TIMEOUT = 120


def load_all() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name in FILES:
        path = HERE / f"{name}.jsonl"
        rows += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return rows


def v1_lowered_ids() -> set[str]:
    """Probe ids for rows the frozen v1 scorer had lowered in its newest iteration."""
    newest = sorted((ROOT / "benchmarks" / "compat" / "iterations").glob("*.json"))[-1]
    data = json.loads(newest.read_text(encoding="utf-8"))
    ids = {f"builtins.{r['id']}" for r in data["builtins"] if r["status"] == "lowered"}
    ids |= {f"stdlib.{r['id']}" for r in data["stdlib_surface"] if r["status"] == "lowered"}
    ids |= {f"stdlib.{r['id']}" for r in data["stdlib_modules"] if r["status"] == "lowered"}
    return ids


def sample(rows: list[dict[str, object]], per_cell: int, lowered: set[str]) -> list[dict[str, object]]:
    cells: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for row in sorted(rows, key=lambda r: str(r["id"])):
        cells.setdefault((str(row["area"]), str(row["category"]), str(row["kind"])), []).append(row)
    picked: dict[str, dict[str, object]] = {}
    for members in cells.values():
        step = max(1, len(members) // per_cell)
        for row in members[::step][:per_cell]:
            picked[str(row["id"])] = row
    for row in rows:
        if str(row["id"]) in lowered or str(row["id"]).endswith(".v1"):
            picked[str(row["id"])] = row
    return [picked[k] for k in sorted(picked)]


def wrapped(row: dict[str, object]) -> str:
    call = ", ".join(repr(a) for a in row["args"])  # type: ignore[attr-defined]
    return f"{row['source']}\n\ndef entry() -> int:\n    return {row['call']}({call})\n"


def oracle(row: dict[str, object]) -> str:
    status, value = engine.run_forked(lambda: engine._exec_probe(str(row["source"]), list(row["args"])))  # type: ignore[call-overload]
    return str(value) if status == "ok" else f"!{status}:{value}"


def run_one(row: dict[str, object]) -> str:
    if row["kind"] == "mention":
        return "mention"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "probe.py"
        src.write_text(wrapped(row), encoding="utf-8")
        cmd = [sys.executable, "-m", "pyscriptc", "compile", str(src), "--run", "--skip-check", "--call", "entry"]
        try:
            proc = subprocess.run(
                [*cmd, "-o", str(Path(tmp) / "bin")], cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUT
            )
        except subprocess.TimeoutExpired:
            return "timeout"
    if proc.returncode == 3:
        return "fence"
    if proc.returncode != 0:
        return "raised" if row["kind"] == "raises" else "build-error"
    if row["kind"] == "raises":
        return "compiled"
    return "pass" if proc.stdout.strip() == oracle(row) else "wrong-output"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-cell", type=int, default=12)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--out", default="")
    parser.add_argument("--cache", default="", help="jsonl of finished ids, so an interrupted run can resume")
    opts = parser.parse_args()
    if sys.platform != "linux":
        raise SystemExit("the compiler only runs on Linux, use the dev container")
    rows = load_all()
    lowered = v1_lowered_ids()
    chosen = sample(rows, opts.per_cell, lowered)
    cache = Path(opts.cache) if opts.cache else None
    done: dict[str, str] = {}
    if cache and cache.exists():
        done = {j["id"]: j["result"] for j in map(json.loads, cache.read_text(encoding="utf-8").splitlines())}

    def cached(row: dict[str, object]) -> str:
        pid = str(row["id"])
        if pid not in done:
            done[pid] = run_one(row)
            if cache:
                with cache.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"id": pid, "result": done[pid]}) + "\n")
        return done[pid]

    with ThreadPoolExecutor(max_workers=opts.workers) as pool:
        results = list(pool.map(cached, chosen))
    cells: dict[str, Counter[str]] = {}
    for row, result in zip(chosen, results, strict=True):
        cells.setdefault(f"{row['area']}/{row['category']}/{row['kind']}", Counter())[result] += 1
    by_id = {str(r["id"]): res for r, res in zip(chosen, results, strict=True)}
    v1 = Counter(by_id[i] for i in sorted(lowered) if i in by_id)
    v1_equiv = Counter(res for i, res in by_id.items() if i.endswith(".v1"))
    report = {
        "sampled": len(chosen),
        "cells": {k: dict(sorted(v.items())) for k, v in sorted(cells.items())},
        "v1_lowered": {
            "ids": len(lowered),
            "v2_probe_outcomes": dict(sorted(v1.items())),
            "v2_probe_missing": sorted(i for i in lowered if i not in by_id),
            "v1_equivalent_probe_outcomes": dict(sorted(v1_equiv.items())),
        },
        "v1_lowered_v2_probe_not_passing": sorted(i for i in lowered if by_id.get(i, "absent") != "pass"),
        "v1_equivalent_not_passing": sorted(i for i, res in by_id.items() if i.endswith(".v1") and res != "pass"),
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    if opts.out:
        Path(opts.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
