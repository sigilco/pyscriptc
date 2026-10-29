"""PS1104 probe: what clearing `object`/`Any` fences would buy (issue #814).

The selfnative report projected 23% -> 59% native definitions from clearing
PS1104. This probe measures it from the tree itself, per fence shape, so the
owner can weigh the two honest options:

  A. a runtime top type (a boxed tagged value in the runtime C): the
     `ps_variant` slice, L-to-XL, overlaps the E3c tagged-union epic;
  B. no top type: `dict[K, Any]` annotations resolve their value type at
     each read site from the checker bridge, bare `Any` stays fenced
     (the `must_fence` gate pins it).

The probe runs `pyscriptc coverage`, groups the PS1104 fences by spelling
and container shape, and counts the definitions whose only root cause is
the family. It is measurement only: no lowering changes, no frozen file.

Usage: uv run python benchmarks/ps1104_probe.py [TARGET] [--json]
Default target is src/pyscriptc (the self-host population the projection
was measured over).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# The PS1104 population splits by the annotation shape at the fence site.
# `dict[K, Any]` is the slice option B covers: the value type is concrete
# at (nearly) every read site and the bridge answers it. Bare `Any` and
# `object` parameters/returns are what option A would have to carry.
SHAPES = (
    ("dict[..., Any]", re.compile(r"dict\[[^\]]*\bAny\b[^\]]*\]")),
    ("list[..., Any]", re.compile(r"list\[[^\]]*\bAny\b[^\]]*\]")),
    ("bare Any", re.compile(r"\bAny\b")),
    ("object", re.compile(r"\bobject\b")),
)


def classify(statement: str) -> str:
    for name, pattern in SHAPES:
        if pattern.search(statement):
            return name
    return "other"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", nargs="?", default="src/pyscriptc", help="file or directory to measure")
    parser.add_argument("--json", action="store_true", help="emit the raw counts as JSON")
    args = parser.parse_args()

    from pyscriptc.coverage import build_report

    report = build_report(Path(args.target))
    fences = [f for f in report.get("fences", []) if f.get("code") == "PS1104"]
    shapes: Counter[str] = Counter()
    for fence in fences:
        shapes[classify(fence.get("statement") or "")] += 1

    defs = report.get("definitions", [])
    touched: set[tuple[str, str]] = set()
    only = set()
    by_index: dict[int, dict] = {i: f for i, f in enumerate(report.get("fences", []))}

    def code_at(index: int | None) -> str | None:
        if index is None:
            return None
        fence = by_index.get(index)
        return fence.get("code") if fence else None

    for definition in defs:
        key = (definition["file"], definition["name"])
        # A definition's fences are indices into the report's fence list.
        codes = {code_at(index) for index in definition.get("fences", [])}
        codes.add(code_at(definition.get("rootCause")))
        codes.discard(None)
        if "PS1104" in codes:
            touched.add(key)
            if codes <= {"PS1104"}:
                only.add(key)

    summary = report.get("summary", {})
    total = summary.get("definitions", len(defs))
    native = summary.get("nativeDefinitions", 0)
    result = {
        "target": str(args.target),
        "definitions": total,
        "native": native,
        "ps1104Fences": len(fences),
        "byShape": dict(shapes),
        "definitionsTouched": len(touched),
        "definitionsBlockedOnlyByPS1104": len(only),
        "projectedNativeIfCleared": native + len(only),
        "projectedShareIfCleared": round(100 * (native + len(only)) / max(total, 1), 1),
        "currentShare": round(100 * native / max(total, 1), 1),
    }
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print(f"target: {result['target']}")
        print(f"definitions: {total}, native: {native} ({result['currentShare']}%)")
        print(f"PS1104 fences: {len(fences)}")
        for shape, count in shapes.most_common():
            print(f"  {shape}: {count}")
        print(f"definitions touched: {len(touched)}")
        print(f"definitions blocked only by PS1104: {len(only)}")
        print(f"if cleared: {result['projectedNativeIfCleared']} native ({result['projectedShareIfCleared']}%)")
        print()
        print("read: the dict[..., Any] slice is the bridge-resolved value type (option B);")
        print("bare Any is the runtime top type (option A), gated on the must_fence probe")
        print("code.PS1104 needing a recorded decision first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
