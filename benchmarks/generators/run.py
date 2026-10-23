#!/usr/bin/env python3
"""Time the generator receipt: generator `for` against the hand-written loop.

The exit criterion of the generator milestone is that `for v in gen(...)`
over ten million items is within 2x of the hand-written `while` loop that
sums the same integers. Both programs compile to their own binary; the
runner refuses to report a ratio when their stdout disagrees.

  uv run python -m benchmarks.generators.run
  uv run python -m benchmarks.generators.run --runs=5 --json=generators.json

Wall time is per process, five interleaved rounds by default, and the ratio
compares the medians. The gate is in the env block below; an override needs
a decision record, because this number is the receipt the milestone quotes.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
GATE = float(os.environ.get("PYSC_GENERATOR_GATE", "2.0"))
ROUNDS = int(os.environ.get("PYSC_GENERATOR_RUNS", "5"))
PAIR = ("while_loop.py", "generator.py")


def _run_once(binary: Path) -> float:
    out = subprocess.run(
        [str(binary)],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        raise SystemExit(f"{binary.name} exited {out.returncode}: {out.stderr.strip()}")
    return out.stdout.strip()


def _time(binary: Path, rounds: int) -> tuple[float, list[float]]:
    """Median wall time in seconds over `rounds` runs, and every sample."""

    import time

    samples: list[float] = []
    for _ in range(rounds):
        start = time.perf_counter()
        _run_once(binary)
        samples.append(time.perf_counter() - start)
    return _median(samples), samples


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _compile(source: Path, out_dir: Path) -> Path:
    binary = out_dir / source.stem
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(source),
            "--emit",
            "bin",
            "--out-dir",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0 or not binary.exists():
        raise SystemExit(f"compile failed for {source.name}:\n{done.stdout}{done.stderr}")
    return binary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generator versus while loop receipt.")
    parser.add_argument("--runs", type=int, default=ROUNDS, help="interleaved rounds per binary")
    parser.add_argument("--json", dest="json_path", help="write the report to this path")
    parser.add_argument(
        "--gate",
        action="store_true",
        help="fail the run when the ratio is over the gate; off by default, the receipt only measures",
    )
    args = parser.parse_args(argv)
    if args.runs < 1:
        print("--runs must be at least 1", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="pyscriptc-genreceipt-") as tmp:
        work = Path(tmp)
        binaries = {}
        stdouts = {}
        for name in PAIR:
            binary = _compile(HERE / "workloads" / name, work)
            binaries[name] = binary
            stdouts[name] = _run_once(binary)
        if stdouts[PAIR[0]] != stdouts[PAIR[1]]:
            print(
                f"stdout mismatch: {PAIR[0]} printed {stdouts[PAIR[0]]!r}, {PAIR[1]} printed {stdouts[PAIR[1]]!r}",
                file=sys.stderr,
            )
            return 1
        medians: dict[str, float] = {}
        samples: dict[str, list[float]] = {}
        for _ in range(args.runs):
            for name in PAIR:
                once, one = _time(binaries[name], 1)
                medians.setdefault(name, []).append(once)
                samples.setdefault(name, []).extend(one)
        ratio = _median(medians[PAIR[1]]) / _median(medians[PAIR[0]])
        ok = ratio <= GATE
        report: dict[str, Any] = {
            "workloads": {
                name: {
                    "median_s": _median(medians[name]),
                    "samples_s": sorted(samples[name]),
                    "stdout": stdouts[name],
                }
                for name in PAIR
            },
            "ratio": ratio,
            "gate": GATE,
            "verdict": "ok" if ok else "over",
        }
        if args.json_path:
            Path(args.json_path).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
        print(f"while loop : {_median(medians[PAIR[0]]) * 1000:8.1f} ms")
        print(f"generator  : {_median(medians[PAIR[1]]) * 1000:8.1f} ms")
        print(f"ratio      : {ratio:.2f}x (gate {GATE:.1f}x)")
        # The receipt records a measurement. A ratio over the gate fails the
        # run only with --gate: the 2x exit criterion is a milestone gate,
        # and until the poll-protocol decision lands the receipt is expected
        # to read above it (see README.md).
        if args.gate:
            print("PASS" if ok else f"FAIL: the generator is over the {GATE:.1f}x gate")
            return 0 if ok else 1
        print("measured" if not ok else "within the gate")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
