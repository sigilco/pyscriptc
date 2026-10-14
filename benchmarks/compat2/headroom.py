"""Headroom check: every denominator row must be able to reach `pass`.

A row is capped when no probe can ever score it better than partial. The scorer
refuses to start while any row is capped.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable

from benchmarks.compat2.loader import load_probes
from benchmarks.compat2.outcomes import Probe, Row


def classify(probe: Probe) -> Row:
    reason = ""
    if probe.kind == "mention":
        reason = "mention probe cannot be value-checked"
    elif not probe.source.strip():
        reason = "empty probe source"
    elif probe.kind == "raises" and not probe.expect_exc:
        reason = "raises probe without expect_exc"
    return Row(
        id=probe.id,
        area=probe.area,
        category=probe.category,
        outcome="excluded" if reason else "fence",
        reachable=not reason,
        evidence=reason,
    )


def check(probes: Iterable[Probe]) -> list[Row]:
    return [classify(probe) for probe in probes]


def capped(probes: Iterable[Probe]) -> list[Row]:
    return [row for row in check(probes) if not row.reachable]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report capped (unreachable) rows.")
    parser.add_argument("probes", help="probe JSONL file or directory")
    args = parser.parse_args(argv)
    probes = list(load_probes(args.probes))
    bad = capped(probes)
    for row in bad:
        print(f"capped {row.id} [{row.category}]: {row.evidence}", file=sys.stderr)
    print(f"headroom: {len(probes) - len(bad)}/{len(probes)} reachable, {len(bad)} capped")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
