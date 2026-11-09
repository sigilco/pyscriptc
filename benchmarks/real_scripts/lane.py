"""The R0 lane: the real-script corpus as a rung in the adoption ladder.

The ladder (`benchmarks/scripts_corpus/ladder.py`) publishes one rate per
rung. This module adds the R0 rung: the vendored third-party scripts under
`benchmarks/real_scripts/scripts`, measured by `benchmarks.real_scripts.measure`.

It carries the pass rate with its denominator (the corpus size), the blocker
ranking and the root-cause ranking, and the last 30 nightly values, so the
trend is readable without a second history mechanism.

    uv run python -m benchmarks.real_scripts.lane --measure nightly/r0.json

This module sits outside the frozen v2 gates. It never writes to
`benchmarks/compat2/` and never reads the scorer holdout.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCRIPTS = HERE / "scripts"

# The ladder document the R0 point rides in.
DEFAULT_PATH = ROOT / "data" / "compat" / "ladder.json"

# The trend window: the last 30 nightly values per rung.
HISTORY_LIMIT = 30

# How many blockers the published point carries. The full ranking lives in
# the measure JSON; the published point keeps the head of the work list.
BLOCKER_LIMIT = 10


def measure_json(measure_path: Path | None = None) -> dict[str, Any]:
    """The measure module output, read from a file or measured here.

    With a path, the JSON the measure module already wrote is read. Without
    one, the same measurement runs in this process.
    """
    if measure_path is not None and measure_path.exists():
        data = json.loads(measure_path.read_text(encoding="utf-8"))
    else:
        from benchmarks.real_scripts.measure import (
            analyse_all,
            blocker_ranking,
            import_census,
            module_ranking,
            root_cause_ranking,
        )

        results = analyse_all(SCRIPTS)
        data = {
            "generated": datetime.now(UTC).date().isoformat(),
            "scripts": [result.to_json() for result in results],
            "blockers": blocker_ranking(results),
            "rootCauses": root_cause_ranking(results),
            "modules": module_ranking(results),
            "imports": import_census(results),
        }
    return data if isinstance(data, dict) else {}


def r0_point(measure_path: Path | None = None) -> dict[str, Any]:
    """The R0 point for one measurement: the rate, the rankings, the size."""
    data = measure_json(measure_path)
    scripts = data.get("scripts", [])
    total = len(scripts)
    passed = sum(1 for script in scripts if script.get("compilePass"))
    fences = sum(int(script.get("fences", 0)) for script in scripts)
    date = data.get("generated") or datetime.now(UTC).date().isoformat()
    return {
        "date": str(date)[:10],
        "rung": "R0",
        "label": "Vendored real script",
        "corpus": "benchmarks/real_scripts/scripts",
        "total": total,
        "compiled": passed,
        "passed": passed,
        "rate": round(passed / total, 4) if total else 0.0,
        "fences": fences,
        "blockers": [
            {
                "family": row.get("family", ""),
                "rootCause": row.get("rootCause", ""),
                "scriptsBlocked": row.get("scriptsBlocked", 0),
                "scriptsAlone": row.get("scriptsAlone", 0),
            }
            for row in data.get("blockers", [])[:BLOCKER_LIMIT]
        ],
        "rootCauses": [
            {
                "cause": row.get("cause", ""),
                "scriptsReleased": row.get("scriptsReleased", 0),
            }
            for row in data.get("rootCauses", [])
        ],
    }


def history_point(point: dict[str, Any]) -> dict[str, Any]:
    """The history shape of one point: the date, the rate and its denominator."""
    return {
        "date": point["date"],
        "passed": point["passed"],
        "total": point["total"],
        "rate": point["rate"],
    }


def merge_history(previous: list[Any], point: dict[str, Any], limit: int = HISTORY_LIMIT) -> list[dict[str, Any]]:
    """The previous trend plus the current point, capped at the limit.

    A point whose date already sits in the trend replaces that point, so one
    date holds one value even when the nightly runs twice.
    """
    history = [row for row in previous if isinstance(row, dict)]
    dates = {row.get("date") for row in history}
    if point["date"] in dates:
        history = [row for row in history if row.get("date") != point["date"]]
    history.append(history_point(point))
    history.sort(key=lambda row: str(row.get("date", "")))
    return history[-limit:]


def build(
    measure_path: Path | None = None,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The R0 section for a ladder document, with its trend."""
    point = r0_point(measure_path)
    previous = previous or {}
    r0 = previous.get("r0") if isinstance(previous.get("r0"), dict) else {}
    history = merge_history(r0.get("history", []), point)
    return {**point, "history": history}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="real_scripts.lane", description=__doc__.splitlines()[0])
    parser.add_argument("--measure", type=Path, default=None, help="the measure module JSON output to read")
    parser.add_argument("--check", action="store_true", help="print the point instead of writing it")
    args = parser.parse_args(argv)
    section = build(args.measure)
    if args.check:
        print(json.dumps(section, indent=2))
        return 0
    print(json.dumps(section, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
