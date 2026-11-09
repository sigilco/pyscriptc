"""Update `data/compat/ladder.json` with the R0 point from the real scripts.

The R0 rung (the vendored third-party scripts of `benchmarks/real_scripts`)
rides in the same ladder document as R1, R2 and R4, with the last 30 nightly
values as its trend. The measurement itself lives in
`benchmarks.real_scripts.lane`; this script only merges the fresh point into
the committed ladder document.

    uv run python scripts/r0_lane.py                            # update in place
    uv run python scripts/r0_lane.py --json data/compat/ladder.json --measure /tmp/r0.json
    uv run python scripts/r0_lane.py --check                    # print, do not write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.real_scripts.lane import DEFAULT_PATH, build  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="r0_lane", description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path, default=DEFAULT_PATH, help="the ladder document to update in place")
    parser.add_argument("--measure", type=Path, default=None, help="the measure module JSON output to read")
    parser.add_argument("--check", action="store_true", help="print the point instead of writing")
    args = parser.parse_args(argv)
    previous: dict[str, Any] = {}
    if args.json.exists():
        try:
            previous = json.loads(args.json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = {}
    if not isinstance(previous, dict):
        previous = {}
    section = build(args.measure, previous)
    if args.check:
        print(json.dumps(section, indent=2))
        return 0
    args.json.parent.mkdir(parents=True, exist_ok=True)
    previous["r0"] = section
    args.json.write_text(json.dumps(previous, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.json} (r0 {section['passed']}/{section['total']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
