"""Regenerate tests/census_pins.json from the measured self-host census.

Run: uv run python scripts/repin_census.py [--src DIR]

Each key is a fully qualified module (or package `__init__`) and the value is
its measured fence count, written as the per-module ceiling. Keys are sorted,
one per line, so PRs touching different modules merge without conflicts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.compat2.census_self import DEFAULT_SRC, build_self_census

from pyscriptc.package import _discover

PINS = Path(__file__).resolve().parent.parent / "tests" / "census_pins.json"


def module_fences(src: Path) -> dict[str, int]:
    census = build_self_census(src)
    units, _skipped = _discover(src)
    names = [
        ".".join([src.name, *path.relative_to(src).with_suffix("").parts]).removesuffix(".__init__")
        for _name, path, _is_package in units
    ]
    assert len(names) == len(census["packages"])
    return {name: pkg["fences"] for name, pkg in zip(names, census["packages"], strict=True)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    args = parser.parse_args(argv)
    pins = module_fences(args.src.resolve())
    PINS.write_text(json.dumps(dict(sorted(pins.items())), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {PINS} ({len(pins)} modules, {sum(pins.values())} fences)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
