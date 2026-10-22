"""The adoption ladder metrics: R1, R2 and the R4 lane shares.

The ladder is the public story of the compat site: one rate per rung, never a
single "Python compat percent". This module produces the numbers the site
publishes, from the sources the plan names:

| rung | metric | source |
| --- | --- | --- |
| R1 | whole-file pass rate over single-file CLI scripts | `benchmarks/scripts_corpus/scripts/` |
| R2 | pass rate over multi-module trees | `benchmarks/scripts_corpus/trees/` |
| R4 | lane share over the locked dependencies | `benchmarks/compat/top100/src/` |

The runner never writes to `benchmarks/compat2/`, never reads the closed
scorer evaluation set, and never touches a frozen gate file.

    uv run python -m benchmarks.scripts_corpus.ladder             # the table
    uv run python -m benchmarks.scripts_corpus.ladder --json out.json

The `--exec` stage builds a binary for every corpus entry that compiles and
compares it with the CPython recording, so a rate is a rate of correct
binaries and not a rate of fences.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from benchmarks.scripts_corpus.run import OUTCOME_PASS, analyse_all
from benchmarks.scripts_corpus.run import exec_script as exec_r1_script
from benchmarks.scripts_corpus.trees import OUTCOME_PASS as TREE_PASS
from benchmarks.scripts_corpus.trees import analyse_all as analyse_trees
from benchmarks.scripts_corpus.trees import exec_tree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCRIPTS = HERE / "scripts"
TREES = HERE / "trees"
ORACLE = HERE / "oracle"
PINS = ROOT / "benchmarks" / "compat" / "top100" / "src"

# The lanes of the per-import policy table, in the order the plan lists them.
LANES = ("native", "pack", "extern", "island", "absent")


def _r1_metrics(exec_stage: bool) -> dict[str, Any]:
    """The R1 numbers: the corpus size, the compile rate and the pass rate."""

    from benchmarks.scripts_corpus.oracle import INVOCATIONS, fixtures_dir

    results = analyse_all(SCRIPTS)
    if exec_stage:
        for result in results:
            recorded = INVOCATIONS.get(result.slug)
            if not recorded:
                continue
            argv = [arg.replace("{fixtures}", str(fixtures_dir())).replace("{tmp}", "/tmp") for arg in recorded]
            exec_r1_script(result, argv, ORACLE)
    total = len(results)
    compiled = sum(1 for result in results if result.compile_pass)
    passed = sum(1 for result in results if result.exec_outcome == OUTCOME_PASS)
    return {
        "rung": "R1",
        "label": "Single-file script",
        "corpus": "benchmarks/scripts_corpus/scripts",
        "total": total,
        "compiled": compiled,
        "passed": passed,
        "measured": bool(exec_stage),
    }


def _r2_metrics(exec_stage: bool) -> dict[str, Any]:
    """The R2 numbers: the tree corpus size, the compile rate and the pass rate."""

    from benchmarks.scripts_corpus.trees import recorded_invocation

    results = analyse_trees(TREES)
    if exec_stage:
        for result in results:
            exec_tree(result, recorded_invocation(result.slug), ORACLE)
    total = len(results)
    compiled = sum(1 for result in results if result.compile_pass)
    passed = sum(1 for result in results if result.exec_outcome == TREE_PASS)
    return {
        "rung": "R2",
        "label": "Multi-module tree",
        "corpus": "benchmarks/scripts_corpus/trees",
        "total": total,
        "compiled": compiled,
        "passed": passed,
        "measured": bool(exec_stage),
    }


def _lane_counts() -> tuple[dict[str, int], int, int]:
    """The lane counts over the locked dependencies, and the tree counts.

    Returns the per-lane import counts, the number of locked trees and the
    number of trees that reach at least one non-native lane.
    """
    from pyscriptc.imports import build_report

    counts: dict[str, int] = {}
    trees = 0
    mixed = 0
    if not PINS.is_dir():
        return counts, trees, mixed
    for tree in sorted(path for path in PINS.iterdir() if path.is_dir()):
        report = build_report(tree)
        trees += 1
        by_lane: dict[str, int] = report["summary"]["byLane"]
        for lane, number in by_lane.items():
            counts[lane] = counts.get(lane, 0) + number
        if any(lane != "native" for lane in by_lane):
            mixed += 1
    return counts, trees, mixed


def _r4_metrics() -> dict[str, Any]:
    """The R4 numbers: the lane share over the locked dependencies."""

    counts, trees, mixed = _lane_counts()
    total = sum(counts.values())
    lanes = []
    for lane in LANES:
        number = counts.get(lane, 0)
        lanes.append(
            {
                "lane": lane,
                "imports": number,
                "share": round(number / total, 4) if total else 0.0,
            }
        )
    return {
        "rung": "R4",
        "label": "Dependency lanes",
        "corpus": "benchmarks/compat/top100/src",
        "total": total,
        "trees": trees,
        "treesWithForeignLane": mixed,
        "selfContainedShare": round((trees - mixed) / trees, 4) if trees else 0.0,
        "lanes": lanes,
    }


def build(exec_stage: bool = True) -> dict[str, Any]:
    """The ladder document: the R1, R2 and R4 numbers."""

    return {
        "schema": 1,
        "rungs": [_r1_metrics(exec_stage), _r2_metrics(exec_stage)],
        "r4": _r4_metrics(),
    }


def render_table(document: dict[str, Any]) -> str:
    """The ladder as a markdown table."""

    lines = ["| rung | corpus | compiled | matches CPython |", "| --- | --- | --- | --- |"]
    for rung in document["rungs"]:
        total = rung["total"]
        rate = f"{100 * rung['passed'] // max(total, 1)}%" if rung["measured"] else "not measured"
        lines.append(f"| {rung['rung']} {rung['label']} | {total} | {rung['compiled']} | {rate} |")
    r4 = document["r4"]
    lines.append("")
    lines.append("| lane | imports | share |")
    lines.append("| --- | --- | --- |")
    for lane in r4["lanes"]:
        lines.append(f"| {lane['lane']} | {lane['imports']} | {100 * lane['share']:.1f}% |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts_corpus.ladder", description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path, help="write the ladder document as JSON")
    parser.add_argument(
        "--no-exec",
        action="store_true",
        help="report the compile rate only, without building and running the binaries",
    )
    args = parser.parse_args(argv)
    document = build(exec_stage=not args.no_exec)
    print(render_table(document))
    if args.json:
        args.json.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
