"""Stage-2 fixed-point lane: compile src/pyscriptc parse-only, twice, per module.

The lane guards the IR rearchitecture (roadmap R2/R6): the compiler's own
tree is lowered to canonical IR and hashed per module across two runs. The
first state is self-vs-self (the seed binary compiles the tree twice; any
divergence is a real nondeterminism leak). When the self-compiled binary can
compile the tree, the same driver compares seed-vs-self and the per-module
divergence count becomes the headline self-host receipt, trending to zero as
the migration lands.

Run: uv run python -m benchmarks.compat2.fixpoint [--src DIR] [--out FILE]
                                    [--summary FILE]

Exit codes: 0 fixed point held, 1 divergence reported.

`--summary` writes a per-module markdown table (one row per module, match or
DIVERGENT) to the given file, for CI job summaries; the same table is printed
to stdout alongside the usual headline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from benchmarks.compat2.census_self import DEFAULT_SRC
from pyscriptc.package import _discover

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "benchmarks" / "compat2" / "fixpoint.json"


def _module_hashes(src: Path) -> dict[str, str]:
    """Lower every importable module under src, hash each canonical IR.

    One temporary tree: each module is lowered through lower_file on a copy
    under one temp dir, so the canonical path scrub keeps the digest
    location independent.
    """

    from pyscriptc.canonical import hash_module
    from pyscriptc.lower import lower_file

    units, skipped = _discover(src)
    hashes: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="fixpoint-") as tmp:
        for index, (_name, path, _is_pkg) in enumerate(units):
            copy = Path(tmp) / f"m{index}.py"
            copy.write_bytes(path.read_bytes())
            module, _ = lower_file(str(copy))
            module.pop("_notes", None)
            label = path.relative_to(src).as_posix()
            hashes[label] = hash_module(module)
    if skipped:
        print(f"skipped {skipped} module(s) the discovery refuses", file=sys.stderr)
    return hashes


def compare_runs(a: dict[str, str], b: dict[str, str]) -> dict[str, Any]:
    """Per-module divergence report between two hash maps."""

    divergent = sorted(m for m in a.keys() & b.keys() if a[m] != b[m])
    only_a = sorted(a.keys() - b.keys())
    only_b = sorted(b.keys() - a.keys())
    return {
        "modules": len(a.keys() | b.keys()),
        "matched": len(a.keys() & b.keys()) - len(divergent),
        "divergent": divergent,
        "onlyInA": only_a,
        "onlyInB": only_b,
    }


def render_table(report: dict[str, Any]) -> str:
    """Markdown table, one row per module: match, DIVERGENT or one-side-only."""

    lines = ["| module | status |", "| --- | --- |"]
    for module in sorted(set(report["divergent"] + report["onlyInA"] + report["onlyInB"])):
        if module in report["divergent"]:
            status = "DIVERGENT"
        elif module in report["onlyInA"]:
            status = "only in run A"
        else:
            status = "only in run B"
        lines.append(f"| {module} | {status} |")
    matched = report["matched"]
    if matched:
        lines.append(f"| _({matched} further module(s) matched)_ | match |")
    return "\n".join(lines)


def self_vs_self(src: Path) -> dict[str, Any]:
    """Two runs of the seed interpreter over the same tree, compared."""

    first = _module_hashes(src)
    second = _module_hashes(src)
    report = compare_runs(first, second)
    return {
        "mode": "self-vs-self",
        "src": str(src),
        "runA": {"sha256": hashlib.sha256(json.dumps(first, sort_keys=True).encode()).hexdigest()},
        "runB": {"sha256": hashlib.sha256(json.dumps(second, sort_keys=True).encode()).hexdigest()},
        "report": report,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fixpoint")
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--out", type=Path, dest="json_out", default=None)
    parser.add_argument("--summary", type=Path, dest="summary_out", default=None)
    args = parser.parse_args(argv)

    result = self_vs_self(args.src.resolve())
    report = result["report"]
    print(f"mode: {result['mode']}")
    print(f"modules compared: {report['modules']}")
    print(f"matched: {report['matched']}")
    print(f"divergent: {len(report['divergent'])}")
    if report["onlyInA"] or report["onlyInB"]:
        print(f"onlyInA: {len(report['onlyInA'])}  onlyInB: {len(report['onlyInB'])}")
    for module in report["divergent"][:20]:
        print(f"  DIVERGENT {module}")
    if len(report["divergent"]) > 20:
        print(f"  ... and {len(report['divergent']) - 20} more")
    table = render_table(report)
    print(table)
    if args.summary_out is not None:
        args.summary_out.parent.mkdir(parents=True, exist_ok=True)
        args.summary_out.write_text(table + "\n", encoding="utf-8")
        print(f"wrote {args.summary_out}")
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.json_out}")
    return 1 if report["divergent"] else 0


if __name__ == "__main__":
    sys.exit(main())
