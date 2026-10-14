#!/usr/bin/env python3
"""Scaling probes for the gates-v1 subset.

Same shape as scriptc's benchmarks/scaling/run.mjs: each case runs at
increasing sizes under the compiled binary and under CPython, stdout must
match, and the line reports times, the growth exponent of the two largest
sizes, and the speedup against CPython. scriptc reads an in-process timer from
stderr. These probes time wall clock, startup included.

  uv run python -m benchmarks.scaling.run [--only=numeric:sum_squares] [--reps=3]
       [--sizes=1000,10000] [--json=out.json]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any

from benchmarks.harness import compile_call, cpython_stdout, oracle_command, time_command, versus_cpython

HERE = Path(__file__).resolve().parent
S = [20_000, 200_000, 1_000_000, 2_000_000]
CASES: list[tuple[str, str, list[int]]] = [
    ("numeric", "sum_squares", S),
    ("numeric", "floor_sum", S),
    ("numeric", "horner", S),
    ("numeric", "repeat_len", [100, 200, 400, 800]),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scaling probes against CPython.")
    parser.add_argument("--only", help="file:case, file, or case")
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--sizes", help="comma-separated sizes, overriding the catalog")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args(argv)
    if args.reps < 1:
        print("--reps must be at least 1", file=sys.stderr)
        return 2

    selected = _select(args.only)
    if args.sizes:
        override = [int(part) for part in args.sizes.split(",") if part.strip()]
        selected = [(file, name, override) for file, name, _sizes in selected]

    results: list[dict[str, Any]] = []
    failed = False
    with tempfile.TemporaryDirectory(prefix="pyscriptc-scaling-") as tmp:
        work = Path(tmp)
        checked: set[str] = set()
        for file, name, sizes in selected:
            entry, bad = _measure(work, file, name, sizes, args.reps, checked)
            results.append(entry)
            if bad:
                failed = True
            print(_format(entry))
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return 1 if failed else 0


def _select(only: str | None) -> list[tuple[str, str, list[int]]]:
    if not only:
        return list(CASES)
    wanted = {part.strip() for part in only.split(",") if part.strip()}
    chosen = [case for case in CASES if f"{case[0]}:{case[1]}" in wanted or case[0] in wanted or case[1] in wanted]
    if not chosen:
        raise SystemExit(f"no scaling case matches {only}")
    return chosen


def _measure(
    work: Path,
    file: str,
    name: str,
    sizes: list[int],
    reps: int,
    checked: set[str],
) -> tuple[dict[str, Any], bool]:
    source = HERE / f"{file}.py"
    rows: list[dict[str, Any]] = []
    for size in sizes:
        binary = work / f"{file}-{name}-{size}"
        skip_check = file in checked
        compiled = compile_call(source, name, [size], binary, skip_check=skip_check)
        checked.add(file)
        row: dict[str, Any] = {"n": size}
        if compiled.returncode != 0:
            row["native_error"] = _tail(compiled.stderr or compiled.stdout) or f"exit {compiled.returncode}"
            rows.append(row)
            break
        oracle = cpython_stdout(source, name, [size])
        if oracle.returncode != 0:
            row["cpython_error"] = _tail(oracle.stderr) or f"exit {oracle.returncode}"
            rows.append(row)
            break
        native = _best([str(binary)], reps, oracle.stdout)
        cpython = _best_oracle(source, name, size, reps, oracle.stdout)
        row["native_ms"] = native.get("ms")
        row["cpython_ms"] = cpython.get("ms")
        if native.get("error"):
            row["native_error"] = native["error"]
        if cpython.get("error"):
            row["cpython_error"] = cpython["error"]
        if native.get("mismatch") or cpython.get("mismatch"):
            row["mismatch"] = True
        rows.append(row)

    native_exp = _exponent(rows, "native_ms")
    cpython_exp = _exponent(rows, "cpython_ms")
    last = next((row for row in reversed(rows) if _finite(row.get("native_ms"))), None)
    ratio = None
    if last and _finite(last.get("native_ms")) and _finite(last.get("cpython_ms")):
        ratio = last["native_ms"] / max(last["cpython_ms"], 0.01)
    flags: list[str] = []
    if any(row.get("native_error") and not row.get("cpython_error") for row in rows):
        flags.append("FAIL")
    if any(row.get("mismatch") for row in rows):
        flags.append("MISMATCH")
    if (
        native_exp is not None
        and native_exp > 1.3
        and last is not None
        and last["native_ms"] > 5
        and native_exp > (cpython_exp if cpython_exp is not None else 1.0) + 0.25
    ):
        flags.append("SUPERLINEAR")
    if ratio is not None and ratio > 3 and last is not None and last["native_ms"] > 2:
        flags.append(">3x")
    entry = {
        "file": file,
        "case": name,
        "rows": rows,
        "native_exp": native_exp,
        "cpython_exp": cpython_exp,
        "ratio": ratio,
        "flags": flags,
    }
    bad = bool({"FAIL", "MISMATCH"} & set(flags))
    return entry, bad


def _best(command: list[str], reps: int, expected: str) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    for _ in range(reps):
        elapsed, completed = time_command(command)
        if completed.returncode != 0:
            return {"error": _tail(completed.stderr) or f"exit {completed.returncode}"}
        if completed.stdout != expected:
            return {"ms": elapsed, "mismatch": True}
        if best is None or elapsed < best["ms"]:
            best = {"ms": elapsed}
    return best or {"error": "no samples"}


def _best_oracle(source: Path, name: str, size: int, reps: int, expected: str) -> dict[str, Any]:
    return _best(oracle_command(source, name, [size]), reps, expected)


def _exponent(rows: list[dict[str, Any]], key: str) -> float | None:
    usable = [row for row in rows if _finite(row.get(key))]
    if len(usable) < 2:
        return None
    before, after = usable[-2], usable[-1]
    return math.log(max(after[key], 0.05) / max(before[key], 0.05)) / math.log(after["n"] / before["n"])


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def _format(entry: dict[str, Any]) -> str:
    cells = []
    for row in entry["rows"]:
        native = "ERR" if row.get("native_error") or row.get("mismatch") else _fmt(row.get("native_ms"))
        cpython = "ERR" if row.get("cpython_error") else _fmt(row.get("cpython_ms"))
        cells.append(f"{row['n']}:{native}/{cpython}")
    flags = ",".join(entry["flags"])
    return (
        f"{entry['file']}:{entry['case']} {' '.join(cells)} "
        f"exp={_fmt(entry['native_exp'])}/{_fmt(entry['cpython_exp'])} "
        f"speed={_speed(entry['ratio'])} {flags}".rstrip()
    )


def _speed(ratio: Any) -> str:
    if not _finite(ratio) or ratio <= 0:
        return "-"
    return versus_cpython(float(ratio))


def _fmt(value: Any) -> str:
    if not _finite(value):
        return "-"
    if value < 10:
        return f"{value:.2f}"
    return f"{value:.0f}"


def _tail(text: str) -> str:
    text = text.strip()
    if len(text) <= 300:
        return text
    return text[-300:]


if __name__ == "__main__":
    raise SystemExit(main())
