"""Build `data/compat/receipts.json` from a per-target receipt run.

The receipt harness (`benchmarks/receipts/`) writes one JSON document per
target plus the combined document. The page reads one file, so this
builder copies the combined document into the canonical data tree, checks
its shape, and refuses to write a document the page cannot render.

The builder never invents a receipt. A run that produced no document
leaves the committed file alone: the page then shows the last measurement,
never a fabricated one.

    uv run python scripts/build_receipts_json.py --from nightly/receipts/receipts.json
    uv run python scripts/build_receipts_json.py --check

Design doc: .agents/docs/benchmarks/target-receipts.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_SOURCE = ROOT / "nightly" / "receipts" / "receipts.json"
DEFAULT_OUT = ROOT / "data" / "compat" / "receipts.json"

# The receipt schema version the page reads.
SCHEMA = "1"

# The methods the page knows how to label.
METHODS = ("native", "qemu", "wasmtime", "compile-only")


def validate(document: dict[str, Any]) -> list[str]:
    """The problems in a receipt document. An empty list means it is valid.

    The validator is plain data plus a small check, so it runs in the
    same venv as the compiler and needs no third-party dependency.
    """

    problems: list[str] = []
    if document.get("schema") != SCHEMA:
        problems.append(f"schema is {document.get('schema')!r}, expected {SCHEMA!r}")
    for key in ("generated", "commit", "oracle", "host", "targets"):
        if key not in document:
            problems.append(f"missing key {key!r}")
    targets = document.get("targets")
    if not isinstance(targets, list):
        problems.append("targets is not a list")
        return problems
    if not targets:
        problems.append("targets is empty")
    seen: set[str] = set()
    for target in targets:
        if not isinstance(target, dict):
            problems.append("a target is not an object")
            continue
        name = target.get("target")
        if not isinstance(name, str) or not name:
            problems.append("a target has no name")
            continue
        if name in seen:
            problems.append(f"target {name!r} appears twice")
        seen.add(name)
        if target.get("method") not in METHODS:
            problems.append(f"target {name!r} has method {target.get('method')!r}")
        for key in ("built", "ran", "matched", "total"):
            if not isinstance(target.get(key), int):
                problems.append(f"target {name!r} has a non-integer {key!r}")
        if isinstance(target.get("total"), int) and isinstance(target.get("built"), int):
            if target["built"] > target["total"]:
                problems.append(f"target {name!r} built more probes than it has")
        if isinstance(target.get("ran"), int) and isinstance(target.get("matched"), int):
            if target["matched"] > target["ran"]:
                problems.append(f"target {name!r} matched more probes than it ran")
        probes = target.get("probes")
        if not isinstance(probes, list):
            problems.append(f"target {name!r} has no probe list")
            continue
        if isinstance(target.get("total"), int) and len(probes) != target["total"]:
            problems.append(f"target {name!r} has {len(probes)} probes but claims {target['total']}")
        for probe in probes:
            if not isinstance(probe, dict) or not isinstance(probe.get("id"), str):
                problems.append(f"target {name!r} has a malformed probe row")
                continue
            if not isinstance(probe.get("match"), bool):
                problems.append(f"target {name!r} probe {probe.get('id')!r} has a non-boolean match")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_receipts_json", description=__doc__)
    parser.add_argument(
        "--from",
        dest="source",
        metavar="PATH",
        default=str(DEFAULT_SOURCE),
        help="the combined receipt document the harness wrote (default nightly/receipts/receipts.json)",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        default=str(DEFAULT_OUT),
        help="where the page's data file is written (default data/compat/receipts.json)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare the committed file with what the source would produce; exit 1 on a difference",
    )
    args = parser.parse_args(argv)

    source = Path(args.source)
    out = Path(args.out)
    if not source.exists():
        print(f"build_receipts_json: no receipt run at {source}", file=sys.stderr)
        return 2
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        print(f"build_receipts_json: {source} is not valid JSON: {error}", file=sys.stderr)
        return 2
    problems = validate(document)
    if problems:
        for problem in problems:
            print(f"build_receipts_json: {problem}", file=sys.stderr)
        return 1
    payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not out.exists():
            print(f"build_receipts_json: {out} does not exist", file=sys.stderr)
            return 1
        if out.read_text(encoding="utf-8") != payload:
            print(f"build_receipts_json: {out} differs from {source}", file=sys.stderr)
            return 1
        print(f"build_receipts_json: {out} is current")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    # Atomic: an interrupted run never leaves a half-written file for the
    # page to bake in.
    temporary = out.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(out)
    print(f"build_receipts_json: wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
