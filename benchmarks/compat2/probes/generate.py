"""Generate the v2 probe set: `uv run python -m benchmarks.compat2.probes.generate`.

Writes builtins.jsonl, surface.jsonl, modules.jsonl, language.jsonl and MANIFEST.json next to this file.
Output is sorted by probe id, so a run on the same CPython and typeshed reproduces it byte for byte.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version
from pathlib import Path

from benchmarks.compat2.outcomes import WEIGHTS, Probe

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DENOM = json.loads((ROOT / "benchmarks" / "compat" / "denominators.json").read_text(encoding="utf-8"))
PINNED_CPYTHON = "3.14"
WORKERS = 3
MODULE_TIMEOUT = 1800


def run_worker(module: str) -> list[dict[str, object]]:
    proc = subprocess.run(
        [sys.executable, "-m", "benchmarks.compat2.probes.surface", module],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=MODULE_TIMEOUT,
    )
    rows = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
    if proc.returncode != 0:
        print(f"worker {module} exit {proc.returncode}: {proc.stderr.strip()[-300:]}", file=sys.stderr)
    return rows


def split_excluded(rows: list[dict[str, object]]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    kept = [r for r in rows if "probe" in r]
    return kept, [r["excluded"] for r in rows if "excluded" in r]  # type: ignore[misc]


def module_rows() -> list[dict[str, object]]:
    rows = []
    for name in DENOM["stdlib_modules"]:
        source = f"import {name}\n\ndef probe() -> int:\n    return 1\n"
        probe = Probe(f"stdlib.{name}", "modules", "module", "value", source, weight=WEIGHTS["module"])
        rows.append(
            {
                "probe": probe.to_json(),
                "meta": {"id": probe.id, "area": "modules", "category": "module", "kind": "value", "fold": "import"},
            }
        )
    return rows


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    ordered = sorted(rows, key=lambda r: r["probe"]["id"])  # type: ignore[index]
    seen: set[str] = set()
    lines = []
    for row in ordered:
        pid = row["probe"]["id"]  # type: ignore[index]
        if pid in seen:
            raise SystemExit(f"duplicate probe id {pid}")
        seen.add(pid)
        lines.append(json.dumps(row["probe"], sort_keys=True))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    from . import language

    if sys.platform != "linux":
        raise SystemExit("probe generation executes stdlib calls, run it in the Linux dev container")

    builtins_rows = run_worker("builtins")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        surface_parts = list(pool.map(run_worker, [m for m in DENOM["stdlib_modules"] if m != "builtins"]))
    surface, excluded = split_excluded([row for part in surface_parts for row in part])
    modules = module_rows()
    lang, lang_info = language.generate()
    groups = {"builtins": builtins_rows, "surface": surface, "modules": modules, "language": lang}
    for name, rows in groups.items():
        write_jsonl(HERE / f"{name}.jsonl", rows)
    metas = [row["meta"] for rows in groups.values() for row in rows]
    manifest = {
        "cpython": platform.python_version(),
        "cpython_pin": PINNED_CPYTHON,
        "mypy": version("mypy"),
        "total": len(metas),
        "language": lang_info,
        "excluded_methods": sorted(excluded, key=lambda e: str(e["module"])),
        "areas": {
            area: {
                cat: n
                for (a, cat), n in sorted(Counter((m["area"], m["category"]) for m in metas).items())
                if a == area
            }  # type: ignore[index]
            for area in groups
        },
        "kinds": dict(sorted(Counter((m["area"], m["category"], m["kind"]) for m in metas).items()))
        and {
            f"{a}/{c}/{k}": n
            for (a, c, k), n in sorted(Counter((m["area"], m["category"], m["kind"]) for m in metas).items())  # type: ignore[index]
        },
        "folds": {f: n for f, n in sorted(Counter(str(m["fold"]) for m in metas).items())},  # type: ignore[index]
        "mention_reasons": {
            r: n for r, n in sorted(Counter(str(m.get("reason", "")) for m in metas if m["kind"] == "mention").items())
        },  # type: ignore[attr-defined,index]
    }
    (HERE / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("total", "areas")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
