"""Compiler re-architecture progress across seams (epic #817).

Prints, per stage seam, counters that must trend down or up as slices land:
module line counts, hand-built `"kind":` node dicts (lowering still dict based),
`["kind"]` dispatch sites (emitters still reading dicts), typed-node users, plus
optional self-host census pins and the state of the epic's task issues.

    uv run python scripts/compiler_progress.py [--json] [--issues] [--census OUT.json] [--summary FILE]
        [--markdown FILE] [--baseline FILE] [--snapshot FILE]

`--summary FILE` appends a markdown table (per-seam totals and the ten modules
holding the most self-host fences, read from `tests/census_pins/`) to FILE,
for `$GITHUB_STEP_SUMMARY` in CI.

`--markdown FILE` appends a trend table: per-metric current value and delta
against a baseline snapshot (`--baseline FILE`). Without `--baseline` the
snapshot is taken from `git origin/master~20`, and `--snapshot FILE` writes
the current snapshot JSON for the next run's baseline. The table includes
MIR leaf-slice coverage over the visible corpus scripts
(`scripts/mir_coverage.py`) and its top out-of-slice reasons.
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


def pins_path(root: Path) -> Path:
    """The census pins location: the per-module shard dir, or the legacy single file."""
    shards = root / "tests" / "census_pins"
    return shards if shards.is_dir() else root / "tests" / "census_pins.json"


def load_pins(path: Path) -> dict[str, int]:
    """Aggregate census pins from a shard directory or the legacy single JSON file."""
    if path.is_dir():
        pins: dict[str, int] = {}
        for shard in sorted(path.glob("*.json")):
            pins.update(json.loads(shard.read_text(encoding="utf-8")))
        return pins
    return json.loads(path.read_text(encoding="utf-8"))


def summary_markdown(totals: dict[str, dict[str, int]], pins: Path) -> str:
    lines = [
        "### Re-architecture progress by seam",
        "",
        "| seam | lines | dict builds | dict reads | typed uses |",
        "|---|---:|---:|---:|---:|",
    ]
    for seam, t in totals.items():
        lines.append(f"| {seam} | {t['lines']} | {t['dict_build']} | {t['dict_read']} | {t['typed_use']} |")
    if pins.exists():
        pins_map: dict[str, int] = load_pins(pins)
        top = sorted(pins_map.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        lines += [
            "",
            f"### Self-host fences: {sum(pins_map.values())} across {len(pins_map)} modules",
            "",
            "| module | fences |",
            "|---|---:|",
        ]
        lines += [f"| {name} | {count} |" for name, count in top]
    return "\n".join(lines) + "\n"


TREND_SEAMS = ("lower", "emit", "mir", "cli", "other")


def fence_seams(pins: Path) -> dict[str, int]:
    """Split self-host fence pins into per-seam buckets (lower, emit, mir, cli, other)."""
    if not pins.exists():
        return {}
    pins_map: dict[str, int] = load_pins(pins)
    out = dict.fromkeys(TREND_SEAMS, 0)
    seam_re = re.compile(r"\w+\.(lower|emit|mir|cli)\b")
    for name, count in pins_map.items():
        match = seam_re.match(name)
        seam = match.group(1) if match else "other"
        out[seam] += count
    return out


def _load_mir_coverage():
    """Load scripts/mir_coverage.py without making `scripts` a package."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("mir_coverage", ROOT / "scripts" / "mir_coverage.py")
    if spec is None or spec.loader is None:
        raise ImportError("scripts/mir_coverage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mir_coverage_summary() -> dict[str, int]:
    """Current MIR slice coverage counters (visible corpus scripts only)."""
    return _load_mir_coverage().coverage_summary()


def mir_coverage_and_reasons() -> tuple[dict[str, int], dict[str, int]]:
    """Coverage counters and the top out-of-slice reasons from one corpus walk."""
    mod = _load_mir_coverage()
    report = mod.measure_corpus()
    return mod.coverage_summary(report=report), mod.top_reasons(report)


def snapshot(
    totals: dict[str, dict[str, int]],
    pins_path: Path,
    mir_coverage: dict[str, int] | None = None,
    mir_reasons: dict[str, int] | None = None,
) -> dict[str, object]:
    """A JSON-serializable baseline snapshot: per-seam totals plus per-seam fences."""
    snap: dict[str, object] = {"totals": totals, "fences": fence_seams(pins_path)}
    if mir_coverage is not None:
        snap["mir_coverage"] = mir_coverage
    if mir_reasons is not None:
        snap["mir_reasons"] = mir_reasons
    return snap


def git_baseline_snapshot() -> dict[str, object] | None:
    """Snapshot of the compiler tree at origin/master~20, computed via git worktree."""
    commit = subprocess.run(
        ["git", "rev-parse", "origin/master~20"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if commit.returncode != 0:
        return None
    checkout = subprocess.run(
        ["git", "worktree", "add", "--detach", str(ROOT / ".progress-baseline"), commit.stdout.strip()],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if checkout.returncode != 0:
        return None
    try:
        base = ROOT / ".progress-baseline"
        rows: dict[str, dict[str, int]] = {}
        seam_dir = base / "src" / "pyscriptc"
        for seam in ("lower", "mir", "emit"):
            d = seam_dir / seam
            for path in sorted(d.rglob("*.py")) if d.is_dir() else []:
                text = path.read_text(encoding="utf-8")
                t = rows.setdefault(seam, {"lines": 0, "dict_build": 0, "dict_read": 0, "typed_use": 0})
                t["lines"] += text.count("\n") + 1
                t["dict_build"] += len(DICT_BUILD.findall(text))
                t["dict_read"] += len(DICT_READ.findall(text))
                t["typed_use"] += len(TYPED_USE.findall(text))
        pins = pins_path(base)
        fences: dict[str, int] = {}
        if pins.exists():
            seam_re = re.compile(r"\w+\.(lower|emit|mir|cli)\b")
            for name, count in load_pins(pins).items():
                match = seam_re.match(name)
                seam = match.group(1) if match else "other"
                fences[seam] = fences.get(seam, 0) + count
        return {"totals": rows, "fences": fences}
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(ROOT / ".progress-baseline")],
            capture_output=True,
            text=True,
            timeout=120,
        )


def markdown_trend(current: dict[str, object], baseline: dict[str, object] | None, baseline_label: str) -> str:
    """Markdown trend table: metric, current, baseline, delta, per-seam fence breakdown."""
    cur_totals = current["totals"]
    base_totals = (baseline or {}).get("totals") or {}

    def flat(totals: object) -> dict[str, int]:
        out: dict[str, int] = {}
        for seam, t in (totals or {}).items():
            for k, v in t.items():
                out[f"{seam}.{k}"] = int(v)
        return out

    cur, base = flat(cur_totals), flat(base_totals)
    keys = sorted(set(cur) | set(base))
    lines = [
        "### Re-architecture trend",
        "",
        f"Baseline: {baseline_label}",
        "",
        "| metric | current | baseline | delta |",
        "|---|---:|---:|---:|",
    ]
    for k in keys:
        c, b = cur.get(k, 0), base.get(k, 0)
        lines.append(f"| {k} | {c} | {b} | {c - b:+d} |")
    cur_f = current.get("fences") or {}
    base_f = (baseline or {}).get("fences") or {}
    lines += [
        "",
        "### Self-host fences by seam",
        "",
        "| seam | current | baseline | delta |",
        "|---|---:|---:|---:|",
    ]
    for seam in TREND_SEAMS:
        c, b = cur_f.get(seam, 0), base_f.get(seam, 0)
        lines.append(f"| {seam} | {c} | {b} | {c - b:+d} |")
    raw_cur_m = current.get("mir_coverage")
    raw_base_m = (baseline or {}).get("mir_coverage")
    cur_m = raw_cur_m if isinstance(raw_cur_m, dict) else {}
    base_m = raw_base_m if isinstance(raw_base_m, dict) else {}
    lines += [
        "",
        "### MIR slice coverage",
        "",
        "In-slice over non-fenced IR functions on the visible corpus scripts",
        "(`benchmarks/compat2/corpus/scripts`, `scripts/mir_coverage.py`).",
        "",
        "| metric | current | baseline | delta |",
        "|---|---:|---:|---:|",
    ]
    for key in ("percent", "in_slice", "functions", "script_count", "out_of_slice", "reason_kinds"):
        c, b = int(cur_m.get(key, 0)), int(base_m.get(key, 0))
        lines.append(f"| mir.{key} | {c} | {b} | {c - b:+d} |")
    raw_cur_r = current.get("mir_reasons")
    raw_base_r = (baseline or {}).get("mir_reasons")
    cur_r = raw_cur_r if isinstance(raw_cur_r, dict) else {}
    base_r = raw_base_r if isinstance(raw_base_r, dict) else {}
    if cur_r:
        lines += [
            "",
            "### MIR out-of-slice reasons (top 10)",
            "",
            "Innermost node the slice predicates reject, per non-fenced out-of-slice function.",
            "",
            "| reason | current | baseline | delta |",
            "|---|---:|---:|---:|",
        ]
        for reason, count in cur_r.items():
            c, b = int(count), int(base_r.get(reason, 0))
            lines.append(f"| `{reason}` | {c} | {b} | {c - b:+d} |")
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
        except (subprocess.SubprocessError, OSError):
            line = f"#{n} unknown"
        out.append(line.replace("#0 ", f"#{n} "))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--issues", action="store_true")
    ap.add_argument("--census", type=Path, default=None)
    ap.add_argument("--summary", type=Path, default=None, help="append a markdown summary to FILE")
    ap.add_argument("--markdown", type=Path, default=None, help="append a markdown trend table to FILE")
    ap.add_argument("--baseline", type=Path, default=None, help="snapshot JSON to diff against")
    ap.add_argument("--snapshot", type=Path, default=None, help="write the current snapshot JSON to FILE")
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
            handle.write(summary_markdown(totals, pins_path(ROOT)))
    coverage: dict[str, int] | None = None
    reasons: dict[str, int] | None = None
    if args.snapshot is not None or args.markdown is not None:
        coverage, reasons = mir_coverage_and_reasons()
    if args.snapshot is not None:
        args.snapshot.write_text(
            json.dumps(snapshot(totals, pins_path(ROOT), coverage, reasons), indent=2) + "\n",
            encoding="utf-8",
        )
    if args.markdown is not None:
        baseline: dict[str, object] | None = None
        label = "origin/master~20"
        if args.baseline is not None and args.baseline.is_file():
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            label = str(args.baseline)
        else:
            baseline = git_baseline_snapshot()
        with args.markdown.open("a", encoding="utf-8") as handle:
            handle.write(
                markdown_trend(
                    snapshot(totals, pins_path(ROOT), coverage, reasons),
                    baseline,
                    label,
                )
            )
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
