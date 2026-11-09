#!/usr/bin/env python3
"""MIR leaf-slice coverage over the visible compat2 script corpus.

For each program under `benchmarks/compat2/corpus/scripts` (the visible
scripts split only), run `lower_file` and test `supported_fn` on every
non-fenced IR function. Prints in-slice over that total, with a per-script
breakdown and the top out-of-slice reasons. A reason names the innermost
node the slice predicates reject (`expr:call`, `stmt:for`, `param:list`,
...); it is located by calling the same `supported_*` predicates on the
children, so the verdict and the reason cannot drift apart.

    uv run python scripts/mir_coverage.py
    uv run python scripts/mir_coverage.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCRIPTS = ROOT / "benchmarks" / "compat2" / "corpus" / "scripts"


def script_mains(scripts_root: Path) -> list[Path]:
    """Visible corpus `main.py` files, sorted for a deterministic report."""
    return sorted(path for path in scripts_root.glob("*/main.py") if path.is_file())


def _fenced_names(module: dict[str, object]) -> set[str]:
    names: set[str] = set()
    fences = module.get("fences") or []
    if not isinstance(fences, list):
        return names
    for fence in fences:
        if not isinstance(fence, dict):
            continue
        function = fence.get("function")
        if isinstance(function, str) and function:
            names.add(function)
    return names


TOP_REASONS = 10


def out_of_slice_reason(fn: dict[str, object]) -> str | None:
    """Why `supported_fn` rejects `fn`, or None when it is in the slice.

    The canonical walk lives in `pyscriptc.emit._mir_in` (the B11 adapter
    module, next to the lane it feeds); this wrapper keeps the script's
    call sites and report vocabulary unchanged.
    """
    from pyscriptc.emit._mir_in import out_of_slice_reason as _adapter_reason

    return _adapter_reason(fn)


def measure_script(path: Path) -> dict[str, object]:
    """Lower one script and score each non-fenced IR function.

    The slice test is the module-level one: a function whose calls all
    name in-slice callees joins the slice with them, so the verdict and
    the per-function reason cannot drift from `lower_module`'s skip list.
    """
    from pyscriptc.lower import lower_file
    from pyscriptc.mirlower import lower_module, supported_module_fn

    module, _source = lower_file(str(path))
    fenced = _fenced_names(module)
    functions = module.get("functions") or []
    if not isinstance(functions, list):
        functions = []
    mir = lower_module(module)
    mir_names = {fn.name for fn in mir.functions}
    counted: list[str] = []
    in_slice: list[str] = []
    reasons: dict[str, str] = {}
    for fn in functions:
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not isinstance(name, str) or name in fenced:
            continue
        counted.append(name)
        reason = out_of_slice_reason(fn)
        if (
            reason is None
            and name in mir_names
            and supported_module_fn(
                fn,
                {f["name"]: f for f in functions if f.get("name") in mir_names},
                {f["name"]: f for f in functions},
            )
        ):
            in_slice.append(name)
        else:
            if reason is None:
                reason = "fn:callee out of slice"
            reasons[name] = reason
    counted.sort()
    in_slice.sort()
    return {
        "script": path.parent.name,
        "path": str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path),
        "functions": len(counted),
        "in_slice": len(in_slice),
        "names": counted,
        "in_slice_names": in_slice,
        "out_of_slice": dict(sorted(reasons.items())),
    }


def measure_corpus(scripts_root: Path | None = None) -> dict[str, object]:
    """Score every visible corpus script. Never walks any other split."""
    root = DEFAULT_SCRIPTS if scripts_root is None else scripts_root
    rows = [measure_script(path) for path in script_mains(root)]
    functions = sum(int(row["functions"]) for row in rows)
    in_slice = sum(int(row["in_slice"]) for row in rows)
    percent = 0 if functions == 0 else int(round(100 * in_slice / functions))
    counts: dict[str, int] = {}
    for row in rows:
        row_reasons = row["out_of_slice"]
        if isinstance(row_reasons, dict):
            for reason in row_reasons.values():
                counts[str(reason)] = counts.get(str(reason), 0) + 1
    return {
        "scripts": rows,
        "reasons": dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))),
        "script_count": len(rows),
        "functions": functions,
        "in_slice": in_slice,
        "percent": percent,
    }


def format_report(report: dict[str, object]) -> str:
    """Stable text table: sorted scripts, fixed columns, trailing newline."""
    lines = [
        "MIR slice coverage (visible corpus scripts)",
        "",
        f"{'script':24} {'in_slice':>8} {'functions':>9}  names",
    ]
    rows = report.get("scripts") or []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            names = row.get("in_slice_names") or []
            name_s = ",".join(names) if isinstance(names, list) else ""
            lines.append(f"{str(row['script']):24} {int(row['in_slice']):8d} {int(row['functions']):9d}  {name_s}")
    lines.append(f"{'TOTAL':24} {int(report['in_slice']):8d} {int(report['functions']):9d}  {int(report['percent'])}%")
    reasons = top_reasons(report)
    if reasons:
        lines += ["", f"Out-of-slice reasons (top {TOP_REASONS})", "", f"{'count':>5}  reason"]
        for reason, count in reasons.items():
            lines.append(f"{count:5d}  {reason}")
    return "\n".join(lines) + "\n"


def top_reasons(report: dict[str, object], limit: int = TOP_REASONS) -> dict[str, int]:
    """The `limit` most common out-of-slice reasons, count descending then name."""
    reasons = report.get("reasons") or {}
    if not isinstance(reasons, dict):
        return {}
    ranked = sorted(((str(k), int(v)) for k, v in reasons.items()), key=lambda item: (-item[1], item[0]))
    return dict(ranked[:limit])


def coverage_summary(scripts_root: Path | None = None, report: dict[str, object] | None = None) -> dict[str, int]:
    """Compact counters for the compiler-progress trend snapshot."""
    if report is None:
        report = measure_corpus(scripts_root)
    reasons = report.get("reasons") or {}
    return {
        "out_of_slice": int(report["functions"]) - int(report["in_slice"]),
        "reason_kinds": len(reasons) if isinstance(reasons, dict) else 0,
        "functions": int(report["functions"]),
        "in_slice": int(report["in_slice"]),
        "percent": int(report["percent"]),
        "script_count": int(report["script_count"]),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="write the report as JSON")
    ap.add_argument(
        "--scripts",
        type=Path,
        default=None,
        help="scripts directory (default: benchmarks/compat2/corpus/scripts)",
    )
    args = ap.parse_args(argv)
    report = measure_corpus(args.scripts)
    if args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        print()
        return 0
    sys.stdout.write(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
