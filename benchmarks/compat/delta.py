"""Compare two compat snapshots, and a runtime report when one was recorded.

Fence rows are not timings. A runtime report that marks a workload ``fence``
is skipped. Printed speedups are ``Nx CPython``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare compat iteration snapshots.")
    parser.add_argument("older", nargs="?", help="earlier snapshot JSON")
    parser.add_argument("newer", nargs="?", help="later snapshot JSON")
    parser.add_argument("--runtime", help="runtime report JSON for the newer iteration")
    parser.add_argument("--previous-runtime", help="runtime report JSON for the older iteration")
    args = parser.parse_args(argv)
    older_path, newer_path = _default_pair(args.older, args.newer)
    older = json.loads(older_path.read_text(encoding="utf-8"))
    newer = json.loads(newer_path.read_text(encoding="utf-8"))
    print(f"compat {older_path.name} -> {newer_path.name}")
    for key in ("language", "builtins", "stdlib_modules", "stdlib_surface", "top100"):
        before = older["scores"][key]
        after = newer["scores"][key]
        delta = after["percent"] - before["percent"]
        sign = "+" if delta >= 0 else ""
        before_n = before.get("lowered_or_subset", before["lowered"])
        after_n = after.get("lowered_or_subset", after["lowered"])
        print(
            f"  {key}: {before['percent']:.2f}% -> {after['percent']:.2f}% ({sign}{delta:.2f}) "
            f"lowered {before_n}/{before['total']} -> {after_n}/{after['total']}"
        )
    _print_runtime(args.previous_runtime, args.runtime)
    return 0


def _default_pair(older: str | None, newer: str | None) -> tuple[Path, Path]:
    folder = HERE / "iterations"
    if older and newer:
        return Path(older), Path(newer)
    files = sorted(folder.glob("*.json"))
    files = [path for path in files if "runtime" not in path.name]
    if len(files) < 2:
        raise SystemExit("need two iteration snapshots")
    return files[0], files[1]


def _print_runtime(previous: str | None, current: str | None) -> None:
    if not current:
        print("runtime: no report passed")
        return
    report = json.loads(Path(current).read_text(encoding="utf-8"))
    prior: dict[str, Any] = {}
    if previous:
        prior_report = json.loads(Path(previous).read_text(encoding="utf-8"))
        prior = {row["name"]: row for row in prior_report.get("workloads", [])}
    print("runtime (speedup is Nx CPython; fences are not timed)")
    for row in report.get("workloads", []):
        name = row["name"]
        if row.get("status") == "fence" or row.get("expect") == "fence":
            print(f"  {name}: fence, not timed")
            continue
        if row.get("status") != "ok":
            print(f"  {name}: {row.get('status')}")
            continue
        speed = _speed(row.get("ratio"))
        extra = ""
        if name in prior and prior[name].get("status") == "ok":
            extra = f" previous {_speed(prior[name].get('ratio'))}"
        print(f"  {name}: {speed}{extra}")


def _speed(ratio: Any) -> str:
    if not isinstance(ratio, (int, float)) or ratio <= 0:
        return "n/a"
    return f"{1.0 / float(ratio):.1f}x CPython"


if __name__ == "__main__":
    sys.exit(main())
