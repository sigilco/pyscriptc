"""Fence census over the pinned top100 sources, with a set-cover ranking of feature families.

Run: python -m benchmarks.compat2.census [--src DIR] [--json OUT] [--top N]

Each package is run through `pyscriptc coverage`. A package is blocked by the set
of root families among its fences. Derived families (fences that follow from a
fence elsewhere in the package) are assumed to clear once their roots do, so they
are reported but not ranked.

The ranking is greedy. Each step picks the family that completes the most packages,
then the one that clears the most of the remaining families of the packages it
touches (sum of 1/remaining over those packages), then the one with the most fences.

`--json` writes that census plus `generated` (UTC calendar date of the run) and
`command` (the invocation that regenerates the file). `generated` changes when
the command is rerun on a later UTC day. Package fence counts do not.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pyscriptc.coverage import build_report

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SRC = ROOT / "benchmarks" / "compat" / "top100" / "src"


def package_census(path: Path) -> dict[str, Any]:
    report = build_report(path)
    roots: dict[str, int] = {}
    derived: dict[str, int] = {}
    for fence in report["fences"]:
        bucket = derived if fence["derived"] else roots
        bucket[fence["family"]] = bucket.get(fence["family"], 0) + 1
    return {
        "package": path.name,
        "fences": report["summary"]["fences"],
        "definitions": report["summary"]["definitions"],
        "fencedDefinitions": report["summary"]["fencedDefinitions"],
        "rootFamilies": dict(sorted(roots.items(), key=lambda kv: (-kv[1], kv[0]))),
        "derivedFamilies": dict(sorted(derived.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def rank_families(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Greedy set cover over the packages that still have root fences."""
    need = {p["package"]: set(p["rootFamilies"]) for p in packages if p["rootFamilies"]}
    fences: dict[str, int] = {}
    for p in packages:
        for family, count in p["rootFamilies"].items():
            fences[family] = fences.get(family, 0) + count
    remaining = {name: set(families) for name, families in need.items()}
    chosen: set[str] = set()
    completed: list[str] = []
    ranking: list[dict[str, Any]] = []
    while True:
        candidates = {family for families in remaining.values() for family in families}
        if not candidates:
            break

        def score(family: str) -> tuple[int, float, int, str]:
            touched = [families for families in remaining.values() if family in families]
            completes = sum(1 for families in touched if len(families) == 1)
            progress = sum(1 / len(families) for families in touched)
            return (-completes, -progress, -fences[family], family)

        best = min(candidates, key=score)
        newly = sorted(name for name, families in remaining.items() if families == {best})
        touched_names = sorted(name for name, families in remaining.items() if best in families)
        chosen.add(best)
        for name in touched_names:
            remaining[name].discard(best)
        for name in newly:
            del remaining[name]
        completed.extend(newly)
        ranking.append(
            {
                "rank": len(ranking) + 1,
                "family": best,
                "packagesTouched": len(touched_names),
                "fences": fences[best],
                "newlyUnblocked": newly,
                "cumulativeUnblocked": len(completed),
            }
        )
    return ranking


def build_census(src: Path) -> dict[str, Any]:
    dirs = sorted(path for path in src.iterdir() if path.is_dir())
    packages = [package_census(path) for path in dirs]
    clean = [p["package"] for p in packages if p["fences"] == 0]
    return {
        "src": str(src),
        "packages": packages,
        "clean": clean,
        "ranking": rank_families(packages),
    }


def json_payload(census: dict[str, Any], json_out: Path) -> dict[str, Any]:
    """Census document plus the run date and the command that regenerates it."""

    try:
        shown = json_out.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        shown = json_out
    return {
        "generated": datetime.now(UTC).date().isoformat(),
        "command": f"uv run python -m benchmarks.compat2.census --json {shown.as_posix()}",
        **census,
    }


def render(census: dict[str, Any], top: int) -> str:
    lines = ["| package | fences | fenced defs | root families |", "| --- | --- | --- | --- |"]
    for pkg in census["packages"]:
        lines.append(
            f"| {pkg['package']} | {pkg['fences']} | {pkg['fencedDefinitions']}/{pkg['definitions']} "
            f"| {len(pkg['rootFamilies'])} |"
        )
    lines += ["", f"Already clean: {', '.join(census['clean']) or 'none'}", ""]
    lines += [
        "| rank | family | packages touched | fences | unblocks | cumulative |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in census["ranking"][:top]:
        lines.append(
            f"| {item['rank']} | {item['family']} | {item['packagesTouched']} | {item['fences']} "
            f"| {', '.join(item['newlyUnblocked']) or '-'} | {item['cumulativeUnblocked']} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="census")
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--json", type=Path, dest="json_out")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args(argv)
    census = build_census(args.src)
    if args.json_out:
        payload = json_payload(census, args.json_out)
        args.json_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(render(census, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
