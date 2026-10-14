"""v1-equivalent probes for every row the frozen v1 scorer had lowered in its newest iteration.

The v1 harness is imported and its own probe sources are captured, so the bodies are exactly what v1 compiled.
These rows carry weight 0.0: they are a continuity check (a v2 score that loses a v1 lowered row can be
explained), not part of the v2 denominator. Probes with kind "emit" (compile only) have no oracle and are skipped.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from benchmarks.compat import score as v1
from benchmarks.compat2.outcomes import Probe

from . import engine

SUFFIX = ".v1"
ITERATIONS = Path(__file__).resolve().parents[2] / "compat" / "iterations"


def lowered_ids() -> dict[str, set[str]]:
    newest = sorted(ITERATIONS.glob("*.json"))[-1]
    data = json.loads(newest.read_text(encoding="utf-8"))
    return {
        key: {r["id"] for r in data[key] if r["status"] == "lowered"}
        for key in ("builtins", "stdlib_surface", "stdlib_modules")
    }


def _capture() -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def fake(label: str, source: str, probes: list[dict[str, Any]], header: str = "") -> list[dict[str, Any]]:
        captured.extend({**probe, "header": header, "area": label} for probe in probes)
        return []

    real = v1._finish_group
    v1._finish_group = fake  # type: ignore[assignment]
    try:
        v1._score_builtins()
        grouped: dict[str, list[str]] = defaultdict(list)
        for qual in v1.DENOM["stdlib_surface"]:
            grouped[qual.rsplit(".", 1)[0]].append(qual)
        for module, quals in sorted(grouped.items()):
            v1._score_surface_module(module, quals)
    finally:
        v1._finish_group = real  # type: ignore[assignment]
    return captured


def generate() -> tuple[list[dict[str, object]], dict[str, object]]:
    lowered = lowered_ids()
    wanted = {f"builtins.{i}": i for i in lowered["builtins"]} | {f"stdlib.{i}": i for i in lowered["stdlib_surface"]}
    rows: list[dict[str, object]] = []
    skipped: dict[str, list[str]] = {"emit": [], "no-oracle": [], "missing": []}
    seen = set()
    for item in _capture():
        pid = ("builtins." if item["section"] == "builtin" else "stdlib.") + item["id"]
        if pid not in wanted:
            continue
        seen.add(pid)
        if item["kind"] == "emit":
            skipped["emit"].append(pid)
            continue
        source = item["header"] + item["source"]
        runs = [engine.run_forked(lambda src=source, fid=item["fid"]: _call(src, fid)) for _ in range(2)]
        if runs[0][0] != "ok" or runs[0] != runs[1] or not isinstance(runs[0][1], int):
            skipped["no-oracle"].append(pid)
            continue
        area = "builtins" if pid.startswith("builtins.") else "surface"
        probe = Probe(pid + SUFFIX, area, "function", "value", source, call=item["fid"], weight=0.0)
        meta = {"id": probe.id, "area": area, "category": "function", "kind": "value", "fold": "v1"}
        rows.append({"probe": probe.to_json(), "meta": meta})
    skipped["missing"] = sorted(set(wanted) - seen)
    info = {"rows": len(rows), "v1_lowered": len(wanted), **{f"skipped_{k}": sorted(v) for k, v in skipped.items()}}
    return rows, info


def _call(source: str, fid: str) -> object:
    namespace: dict[str, object] = {}
    exec(compile(source, "<v1>", "exec"), namespace)
    return namespace[fid]()  # type: ignore[operator]
