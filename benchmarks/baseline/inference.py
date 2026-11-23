#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Record compile-time inference numbers for the in-repo benchmark trees.

The suite prices the pin engine (issue #1319, stage 1 of the
`.agents/docs/design/inference-perf-baseline.md` note): every workload is
compiled twice, once without inference and once with `--infer`, and the
run records the wall-clock split and the engine's own report. The ratio
gate (`--check`) never reads these rows: `infer_ms` is recorded per
release through `bench.yml` and armed only after two releases of data,
matching the report-only discipline of the existing time and RSS ratios.

Workloads:

- `wordcount-infer` — `examples/wordcount`, fully annotated: the skip
  fast path (issue #1317) must take the engine out, and the row shows
  `infer_ms` near zero with `infer_skipped` set.
- `real-scripts-infer` — `benchmarks/real_scripts/scripts` (39 script
  modules): cold-cache inference on a real mixed-annotation tree.
- `synthetic-150-infer` — a generated ~150-module chain
  (`synthetic.py`), so the suite stays in-repo and hermetic while it
  still scales the way `src/pyscriptc` did in the design note's
  measurement.

A tree that fences still prices its inference: the fences belong to the
tree, not to the engine, so a fenced compile is a measured row. A
compile that fails outright (no summary) is an error row.

  uv run python -m benchmarks.baseline.run --suite inference --json=inf.json
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from benchmarks.baseline.synthetic import write_tree
from benchmarks.harness import repo_root

WORDCOUNT = repo_root() / "examples" / "wordcount"
REAL_SCRIPTS = repo_root() / "benchmarks" / "real_scripts" / "scripts"


def suite_workloads() -> list[dict[str, Any]]:
    """The inference workloads, in the order the runner walks them."""

    return [
        {"name": "wordcount-infer", "source": str(WORDCOUNT), "kind": "package"},
        {"name": "real-scripts-infer", "source": str(REAL_SCRIPTS), "kind": "package"},
        {"name": "synthetic-150-infer", "source": None, "kind": "synthetic"},
    ]


def run_inference_suite(workloads: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """One pass over the inference workloads; each row records one
    compile pair. Nothing here times a compiled binary: the subject is
    the compiler itself."""

    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="pyscriptc-infer-suite-") as tmp:
        work = Path(tmp)
        synthetic_root = work / "synthetic"
        for workload in workloads if workloads is not None else suite_workloads():
            if workload["kind"] == "synthetic":
                source = write_tree(synthetic_root)
            else:
                source = Path(workload["source"])
            rows.append(_run_workload(work, str(workload["name"]), source))
    return rows


def _compile(
    source: Path, *, infer: bool, out: Path, debug_dir: Path
) -> tuple[float, subprocess.CompletedProcess[str]]:
    """One compile of a tree, timed. The IR-only emit keeps the link and
    binary size out of the number: the row prices inference, not
    emission. The debug directory carries the engine's own report."""

    command = [
        sys.executable,
        "-m",
        "pyscriptc",
        "compile",
        str(source),
        "--emit",
        "ir",
        "--skip-check",
        "--debug-dir",
        str(debug_dir),
        "-o",
        str(out),
    ]
    if infer:
        command.append("--infer")
    started = time.perf_counter()
    completed = subprocess.run(command, capture_output=True, text=True)
    return (time.perf_counter() - started) * 1000.0, completed


def _run_workload(work: Path, name: str, source: Path) -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "source": str(source), "status": "error"}
    plain_ms, plain = _compile(
        source,
        infer=False,
        out=work / f"{name}.plain.ir.json",
        debug_dir=work / f"{name}.plain.debug",
    )
    row["compile_ms"] = round(plain_ms, 3)
    plain_summary = _summary(work / f"{name}.plain.debug" / "summary.json")
    if not plain_summary:
        row["detail"] = _tail(plain.stderr or plain.stdout) or f"compile exit {plain.returncode}"
        return row
    row["compile_status"] = plain_summary.get("status")

    infer_ms, inferred = _compile(
        source,
        infer=True,
        out=work / f"{name}.infer.ir.json",
        debug_dir=work / f"{name}.infer.debug",
    )
    row["infer_ms"] = round(infer_ms, 3)
    summary = _summary(work / f"{name}.infer.debug" / "summary.json")
    engine = summary.get("infer") if isinstance(summary.get("infer"), dict) else {}
    if not engine:
        row["detail"] = _tail(inferred.stderr or inferred.stdout) or f"compile exit {inferred.returncode}"
        return row
    row.update(
        {
            "status": "ok",
            "infer_overhead_ratio": infer_ms / max(plain_ms, 1e-9),
            "infer_skipped": engine.get("skipped"),
            "infer_pins": engine.get("pins"),
            "infer_call_sites": engine.get("callSites"),
            "infer_engine_ms": engine.get("ms"),
        }
    )
    return row


def _summary(path: Path) -> dict[str, Any]:
    """A compile's `--debug-dir` summary, or an empty dict when the
    compile wrote none (it failed before the artifacts were flushed)."""

    if not path.is_file():
        return {}
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return summary if isinstance(summary, dict) else {}


def print_rows(rows: list[dict[str, Any]]) -> None:
    """One line per workload: the compile pair and the engine's report."""

    for row in rows:
        if row["status"] != "ok":
            detail = f"  {row['detail']}" if row.get("detail") else ""
            print(f"{row['name']:<22} {row['status']}{detail}")
            continue
        skipped = "skipped" if row.get("infer_skipped") else f"{row.get('infer_pins', 0)} pins"
        print(
            f"{row['name']:<22} "
            f"compile {row['compile_ms']:>8.1f} ms  infer {row['infer_ms']:>8.1f} ms  "
            f"({row['infer_overhead_ratio']:>5.2f}x)  engine {skipped}"
        )


def _tail(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= 400:
        return text
    return text[-400:]


if __name__ == "__main__":
    print_rows(run_inference_suite())
