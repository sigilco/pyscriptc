"""Compiler re-architecture progress across seams (epic #817).

Prints, per stage seam, counters that must trend down or up as slices land:
module line counts, hand-built `"kind":` node dicts (lowering still dict based),
`["kind"]` dispatch sites (emitters still reading dicts), typed-node users, plus
optional self-host census pins and the state of the epic's task issues.

    uv run python scripts/compiler_progress.py [--json] [--issues] [--census OUT.json] [--summary FILE]

`--summary FILE` appends a markdown table (per-seam totals and the ten modules
holding the most self-host fences, read from `tests/census_pins.json`) to FILE,
for `$GITHUB_STEP_SUMMARY` in CI.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "pyscriptc"
EPIC_TASKS = range(818, 834)
DICT_BUILD = re.compile(r"""\{\s*["']kind["']\s*:""")
DICT_READ = re.compile(r"""\[\s*["']kind["']\s*\]""")
TYPED_USE = re.compile(r"\b_ir\.\w+\(|from pyscriptc\._irnode|from \.+_irnode|import _irnode")


def seam_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for seam in ("lower", "mir", "emit"):
        base = SRC / seam
        files = sorted(base.rglob("*.py")) if base.is_dir() else []
        for path in files:
            text = path.read_text(encoding="utf-8")
            rows.append(
                {
                    "seam": seam,
                    "module": str(path.relative_to(SRC)),
                    "lines": text.count("\n") + 1,
                    "dict_build": len(DICT_BUILD.findall(text)),
                    "dict_read": len(DICT_READ.findall(text)),
                    "typed_use": len(TYPED_USE.findall(text)),
                }
            )
    return rows


def census_pins(path: Path | None) -> dict[str, object]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    mods = data.get("modules", data)
    return {"modules": len(mods) if hasattr(mods, "__len__") else None, "fences": data.get("fences")}


def summary_markdown(totals: dict[str, dict[str, int]], pins_path: Path) -> str:
    lines = [
        "### Re-architecture progress by seam",
        "",
        "| seam | lines | dict builds | dict reads | typed uses |",
        "|---|---:|---:|---:|---:|",
    ]
    for seam, t in totals.items():
        lines.append(f"| {seam} | {t['lines']} | {t['dict_build']} | {t['dict_read']} | {t['typed_use']} |")
    if pins_path.is_file():
        pins: dict[str, int] = json.loads(pins_path.read_text(encoding="utf-8"))
        top = sorted(pins.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        lines += [
            "",
            f"### Self-host fences: {sum(pins.values())} across {len(pins)} modules",
            "",
            "| module | fences |",
            "|---|---:|",
        ]
        lines += [f"| {name} | {count} |" for name, count in top]
    return "\n".join(lines) + "\n"


def issue_states() -> list[str]:
    out: list[str] = []
    for n in EPIC_TASKS:
        try:
            line = subprocess.run(
                [
                    "gh",
                    "issue",
                    "view",
                    str(n),
                    "--repo",
                    "sigilco/pyscriptc",
                    "--json",
                    "state,title",
                    "-q",
                    '"#\\(.number // 0) \\(.state) \\(.title)"',
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=20,
            ).stdout.strip()
        except subprocess.SubprocessError, OSError:
            line = f"#{n} unknown"
        out.append(line.replace("#0 ", f"#{n} "))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--issues", action="store_true")
    ap.add_argument("--census", type=Path, default=None)
    ap.add_argument("--summary", type=Path, default=None, help="append a markdown summary to FILE")
    args = ap.parse_args()
    rows = seam_rows()
    totals: dict[str, dict[str, int]] = {}
    for r in rows:
        t = totals.setdefault(str(r["seam"]), {"lines": 0, "dict_build": 0, "dict_read": 0, "typed_use": 0})
        for k in t:
            t[k] += int(r[k])  # type: ignore[call-overload]
    report: dict[str, object] = {"totals": totals, "modules": rows, "census": census_pins(args.census)}
    if args.issues:
        report["issues"] = issue_states()
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(summary_markdown(totals, ROOT / "tests" / "census_pins.json"))
    if args.json:
        json.dump(report, sys.stdout, indent=2)
        print()
        return 0
    print(f"{'module':44} {'lines':>6} {'dict+':>6} {'dict?':>6} {'typed':>6}")
    for r in rows:
        print(f"{r['module']:44} {r['lines']:>6} {r['dict_build']:>6} {r['dict_read']:>6} {r['typed_use']:>6}")
    print()
    for seam, t in totals.items():
        print(f"{seam:8} " + " ".join(f"{k}={v}" for k, v in t.items()))
    if report["census"]:
        print(f"census: {report['census']}")
    for line in report.get("issues", []):  # type: ignore[attr-defined]
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
