"""Per-function IR digests: name the first function that differs between two lowerings.

A whole-module IR hash says only that something moved. This tool hashes each
function and class of a module IR dict with the canonical form
(`pyscriptc.canonical`, no change to its rules or to any frozen baseline), so
an A/B run of two compiler builds reports the units that changed, in module
order, instead of one opaque digest.

    python -m benchmarks.compat2.fndigest A B [--json]

A and B are each a Python source file (lowered here) or an `ir.json` dump
(`pyscriptc compile --debug-dir DIR`). Exit 0 when every unit matches, 1 when
any unit was added, removed or changed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HEADER = "header"
FENCES = "fences"


def _hash(value: Any) -> str:
    from pyscriptc.canonical import hash_module

    return hash_module(value)


def unit_digests(module: dict[str, Any]) -> dict[str, str]:
    """Map `fn:<name>`, `class:<name>`, `header` and `fences` to a canonical digest, in module order.

    A repeated name gets a `#2`, `#3` suffix so two same-named units never collapse.
    """
    out: dict[str, str] = {}

    def add(key: str, value: Any) -> None:
        unique, n = key, 1
        while unique in out:
            n += 1
            unique = f"{key}#{n}"
        out[unique] = _hash({"unit": value})

    skipped = {"functions", "classes", "fences"}
    add(HEADER, {k: v for k, v in module.items() if k not in skipped})
    for fn in module.get("functions", ()):
        add(f"fn:{fn.get('name', '?')}", fn)
    for cls in sorted(module.get("classes", ()), key=lambda c: c.get("name") or ""):
        add(f"class:{cls.get('name', '?')}", cls)
    add(FENCES, module.get("fences", []))
    return out


def diff_units(a: dict[str, str], b: dict[str, str]) -> list[tuple[str, str]]:
    """The (unit, status) pairs that differ, status in changed/added/removed, A's order then B's extras."""
    rows: list[tuple[str, str]] = []
    for key, digest in a.items():
        if key not in b:
            rows.append((key, "removed"))
        elif b[key] != digest:
            rows.append((key, "changed"))
    rows.extend((key, "added") for key in b if key not in a)
    return rows


def load_module(path: Path) -> dict[str, Any]:
    """A module IR dict from an ir.json dump or by lowering a Python source file."""
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    from pyscriptc.lower import lower_file

    module, _ = lower_file(str(path))
    module.pop("_notes", None)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fndigest", description=__doc__.splitlines()[0])
    parser.add_argument("a", type=Path)
    parser.add_argument("b", type=Path)
    parser.add_argument("--json", action="store_true", help="print the differing units as JSON")
    args = parser.parse_args(argv)
    units_a = unit_digests(load_module(args.a))
    units_b = unit_digests(load_module(args.b))
    rows = diff_units(units_a, units_b)
    if args.json:
        print(json.dumps([{"unit": unit, "status": status} for unit, status in rows], indent=1))
    elif rows:
        print(f"{len(rows)} of {len(units_a)} units differ; first: {rows[0][0]} ({rows[0][1]})")
        for unit, status in rows:
            print(f"  {status:8} {unit}")
    else:
        print(f"all {len(units_a)} units match")
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
