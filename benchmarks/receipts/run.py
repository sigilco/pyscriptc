# SPDX-License-Identifier: Apache-2.0
"""Run the per-target probe receipts.

One run compiles every probe for every target in the tier-A matrix,
runs what can run on this host, byte-compares against the pinned CPython
3.14 oracle, and writes the receipt documents.

    uv run python -m benchmarks.receipts.run                       # every target
    uv run python -m benchmarks.receipts.run --outdir nightly/     # into a dir
    uv run python -m benchmarks.receipts.run --target aarch64-linux-musl
    uv run python -m benchmarks.receipts.run --probe int_arith --target wasm32-wasi

The receipt never invents a verdict: a missing runner is a skip with a
reason, a wrong output is a wrong-output row (and, with `--strict`, a
non-zero exit).

Design: .agents/docs/benchmarks/target-receipts.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from benchmarks.receipts.harness import (
    ReceiptDocument,
    measure_all,
    runner_available,
    summary_table,
    write_receipts,
)
from benchmarks.receipts.oracle import resolve_oracle
from benchmarks.receipts.probes import load_probes
from benchmarks.receipts.targets import TARGETS, by_name, runner_for


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="benchmarks.receipts", description=__doc__)
    parser.add_argument(
        "--outdir",
        metavar="DIR",
        default="nightly/receipts",
        help="directory the receipt JSON files are written to (default nightly/receipts)",
    )
    parser.add_argument(
        "--target",
        action="append",
        metavar="NAME",
        help="measure only this target; repeatable",
    )
    parser.add_argument(
        "--probe",
        action="append",
        metavar="ID",
        help="measure only this probe; repeatable",
    )
    parser.add_argument("--oracle", metavar="PYTHON", help="the CPython 3.14 oracle (default: the pinned resolver)")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when a runnable probe did not build, run or match",
    )
    parser.add_argument("--quiet", action="store_true", help="print only the summary table")
    parser.add_argument(
        "--summary",
        metavar="PATH",
        help="write the markdown summary table to this file (the GitHub job summary path)",
    )
    args = parser.parse_args(argv)

    if args.target:
        try:
            chosen_targets = [by_name(name) for name in args.target]
        except KeyError as error:
            print(f"unknown target {error}", file=sys.stderr)
            return 2
    else:
        chosen_targets = list(TARGETS)

    all_probes = load_probes()
    if args.probe:
        wanted = set(args.probe)
        chosen_probes = [probe for probe in all_probes if probe.id in wanted]
        missing = wanted - {probe.id for probe in chosen_probes}
        if missing:
            print(f"unknown probe(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 2
    else:
        chosen_probes = all_probes

    oracle = args.oracle or resolve_oracle()
    if oracle is None:
        print("no CPython 3.14 oracle; the receipt compares against committed expectations only", file=sys.stderr)

    document = measure_all(targets=chosen_targets, probes=chosen_probes, oracle=oracle)
    written = write_receipts(document, Path(args.outdir))
    if not args.quiet:
        for path in written:
            print(f"wrote {path}")
    print(summary_table(document))
    if args.summary:
        summary = Path(args.summary)
        summary.parent.mkdir(parents=True, exist_ok=True)
        summary.write_text(summary_table(document) + "\n", encoding="utf-8")

    if args.strict:
        return _strict_exit(document, chosen_targets)
    return 0


def _strict_exit(document: ReceiptDocument, targets: list) -> int:
    """The strict exit code: every runnable row has to build, run and match.

    A compile-only row is never a failure: it is the documented posture
    of a target this host cannot run. A runnable row that did not match
    is the finding the receipt exists to report.
    """

    failures = 0
    for receipt in document.targets:
        target = by_name(receipt.target)
        if not runner_for(target):
            continue
        if not runner_available(receipt.runner):
            continue
        for row in receipt.probes:
            if not (row.built and row.ran and row.match):
                failures += 1
                print(f"FAIL {receipt.target}/{row.id}: {row.detail or 'no match'}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
