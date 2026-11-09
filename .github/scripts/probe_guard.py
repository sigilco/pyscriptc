#!/usr/bin/env python3
"""Differential-probe guard for the lowering rule (issue #524, audit M3).

The rule: a PR that adds a lowering, a runtime helper or a stdlib pack must add
a differential probe under probes/ that runs the new construct against the
pinned CPython and byte-compares stdout, stderr and the exit code. The escape
hatch is narrow: a construct that is known to diverge records the difference in
the divergence ledger in the same PR.

The classification is pure (a changed-path list in, a verdict out) so it is
unit tested without git. The CLI diffs two refs or takes an explicit list:

  probe_guard.py --base SHA [--head SHA]  classify the diff between the refs
  probe_guard.py --files PATH [PATH ...]  classify an explicit path list
  probe_guard.py --block                  exit 1 on a gap (default: warn only)

Warn mode always exits 0 and emits a ::warning:: annotation under Actions.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass, field

# What counts as "adds a lowering, a runtime helper or a stdlib pack". The
# stdlib packs are the per-module lowerings in restlower/. The MIR slice is
# where new constructs are admitted now, so the op table (mir.py) and the two
# MIR lowering stages count as lowering surface. emit/ stays out: its changes
# are codegen migrations of constructs the earlier stages already admitted.
LOWERING_PATHS = (
    "src/pyscriptc/lower/",
    "src/pyscriptc/restlower/",
    "src/pyscriptc/runtime/",
    "src/pyscriptc/mir.py",
    "src/pyscriptc/mirlower.py",
    "src/pyscriptc/miremit.py",
)

# A differential probe is a committed program under probes/ that a lane
# byte-compares against the pinned CPython. The rule is "new or changed
# probe", so any diff under probes/ satisfies it. .agents/probes/ does not
# count: those are ad-hoc agent scripts, not CPython comparisons.
PROBE_PREFIX = "probes/"

# The escape hatch: the difference is recorded in the same PR.
# divergence-ledger.json is the M1 ledger; divergences.md is the numbered list
# and stands in until the ledger lands.
LEDGER_PATHS = frozenset(
    {
        "docs/reference/divergence-ledger.json",
        "docs/reference/divergences.md",
    }
)


@dataclass
class Verdict:
    lowering: list[str] = field(default_factory=list)
    probes: list[str] = field(default_factory=list)
    ledger: list[str] = field(default_factory=list)

    @property
    def gap(self) -> bool:
        return bool(self.lowering) and not (self.probes or self.ledger)


def classify(changed: list[str]) -> Verdict:
    v = Verdict()
    for raw in changed:
        path = raw.strip()
        if not path:
            continue
        if path.startswith(LOWERING_PATHS):
            v.lowering.append(path)
        if path.startswith(PROBE_PREFIX):
            v.probes.append(path)
        if path in LEDGER_PATHS:
            v.ledger.append(path)
    return v


def report(v: Verdict) -> str:
    shown = ", ".join(v.lowering[:5])
    if len(v.lowering) > 5:
        shown += f" (+{len(v.lowering) - 5} more)"
    return (
        f"lowering change without a differential probe: {shown}. "
        "Add a probe under probes/ that byte-compares the construct against "
        "the pinned CPython, or record the known difference in "
        "docs/reference/divergence-ledger.json in the same PR "
        "(rule: AGENTS.md, issue #524)."
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", help="diff base ref")
    p.add_argument("--head", default="HEAD", help="diff head ref (default HEAD)")
    p.add_argument("--files", nargs="*", help="explicit changed-path list")
    p.add_argument(
        "--block",
        action="store_true",
        help="exit 1 on a gap (default is warn mode: annotate and exit 0)",
    )
    a = p.parse_args(argv)

    if a.files is not None:
        changed = a.files
    elif a.base:
        out = subprocess.run(
            ["git", "diff", "--name-only", a.base, a.head],
            check=True,
            capture_output=True,
            text=True,
        )
        changed = out.stdout.splitlines()
    else:
        p.error("give --files or --base")

    v = classify(changed)
    if not v.lowering:
        print("probe guard: no lowering surface changed")
        return 0
    if not v.gap:
        kind = "probe" if v.probes else "ledger row"
        print(f"probe guard: lowering surface changed, {kind} present")
        return 0
    msg = report(v)
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::warning title=differential probe guard::{msg}")
    else:
        print(f"warning: {msg}", file=sys.stderr)
    return 1 if a.block else 0


if __name__ == "__main__":
    sys.exit(main())
