#!/usr/bin/env python3
"""Micro-benchmark of island marshalling per call (issue #670).

The POC measured whole programs; this measures the seam itself. For every
scalar and composite type the island ABI carries (int, float, bool, str,
bytes, list[int], and the no-argument baseline) it times a typed island
call in a native loop and reports the per-call cost and the marshalling
share: the delta over the no-argument baseline, which covers the GIL
re-entry, the namespace lookup and the call machinery.

    uv run python -m benchmarks.engine_corpus.marshalling [--calls 50000] [--json OUT]

Nothing here gates a build: it is the release-receipt evidence for the
embed epic (recorded in the PR body of #670 and re-run at release
boundaries).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: The island module the harness compiles: one echo function per type.
ISLAND_MODULE = '''\
def sink() -> int:
    return 1


def echo_int(x: int) -> int:
    return x


def echo_float(x: float) -> float:
    return x


def echo_bool(x: bool) -> bool:
    return x


def echo_str(x: str) -> str:
    return x


def echo_bytes(x: bytes) -> bytes:
    return x


def echo_list(x: list[int]) -> list[int]:
    return x
'''

#: One bench line per measured case: the generated loop calls case[1] and
#: tags the timing with case[0]. The `base` case measures the seam without
#: a payload (GIL re-entry, namespace lookup, call machinery), so each
#: type's marshalling share is its delta over `base`.
CASES = [
    ("base", "emod.sink()"),
    ("int", "emod.echo_int(12345)"),
    ("float", "emod.echo_float(1.5)"),
    ("bool", "emod.echo_bool(True)"),
    ("str", 'emod.echo_str("abcdefgh")'),
    ("bytes", 'emod.echo_bytes(b"abcdefgh")'),
    ("list", "emod.echo_list([1, 2, 3, 4])"),
]


def build_harness(work: Path, calls: int, repeats: int) -> Path:
    """Write the island module and the timed entry; return the entry path.

    Each case runs a warm-up loop first and is then timed `repeats` times
    inside the binary; the best run is reported. The warm-up matters: the
    first island call pays the embedded interpreter's initialization (tens
    of milliseconds), which would otherwise dominate a short run, and a
    single timed run on a shared machine is dominated by scheduling noise.
    """
    (work / "pyscriptc.toml").write_text('[imports]\nemod = "island"\n', encoding="utf-8")
    (work / "emod.py").write_text(ISLAND_MODULE, encoding="utf-8")
    lines = [
        "import time",
        "",
        "import emod",
        "",
        "",
        "def main() -> None:",
        f"    n: int = {calls}",
        f"    r: int = {repeats}",
    ]
    for label, call in CASES:
        lines += [
            "    w: int = 0",
            "    while w < n:",
            f"        _ = {call}",
            "        w = w + 1",
            "    best: float = 0.0",
            "    k: int = 0",
            "    while k < r:",
            "        t0: float = time.perf_counter()",
            "        i: int = 0",
            "        while i < n:",
            f"            _ = {call}",
            "            i = i + 1",
            "        t1: float = time.perf_counter()",
            "        per: float = (t1 - t0) / float(n) * 1e9",
            "        if best == 0.0 or per < best:",
            "            best = per",
            "        k = k + 1",
            f'    print("{label}", best)',
        ]
    lines += ["", "", 'if __name__ == "__main__":', "    main()", ""]
    entry = work / "bench.py"
    entry.write_text("\n".join(lines), encoding="utf-8")
    return entry


def measure(calls: int, repeats: int) -> dict[str, float]:
    """Compile the harness, run it, return ns/call per case."""
    with tempfile.TemporaryDirectory(prefix="marshal-bench-") as tmp:
        work = Path(tmp)
        entry = build_harness(work, calls, repeats)
        binary = work / "bench"
        env = {**os.environ, "PYSC_PROGRAM_DIR": str(work)}
        proc = subprocess.run(
            [sys.executable, "-m", "pyscriptc", "compile", str(entry), "--island", "--emit", "bin", "-o", str(binary)],
            cwd=work,
            capture_output=True,
            text=True,
            timeout=600,
            env=env,
        )
        if proc.returncode != 0:
            raise SystemExit(proc.stderr)
        run = subprocess.run(
            [str(binary)],
            cwd=work,
            capture_output=True,
            text=True,
            timeout=600,
            env=env,
        )
        if run.returncode != 0:
            raise SystemExit(run.stderr)
        rows: dict[str, float] = {}
        for line in run.stdout.splitlines():
            name, _, value = line.partition(" ")
            rows[name] = float(value)
        return rows


def summarize(rows: dict[str, float]) -> dict[str, dict[str, float]]:
    """Per type: the per-call cost and the marshalling share over `base`."""
    base = rows.get("base", 0.0)
    out: dict[str, dict[str, float]] = {}
    for name, value in rows.items():
        out[name] = {
            "ns_per_call": round(value, 1),
            "marshal_ns": round(value - base, 1) if name != "base" else 0.0,
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--calls", type=int, default=50000, help="timed calls per case")
    parser.add_argument("--repeats", type=int, default=5, help="timed repetitions per case (best wins)")
    parser.add_argument("--json", dest="json_path", help="write the report to this path")
    args = parser.parse_args(argv)
    rows = measure(args.calls, args.repeats)
    report = summarize(rows)
    base = report["base"]["ns_per_call"]
    print(
        f"island marshalling per call ({args.calls} calls x {args.repeats} repeats, "
        "best run, ns/call):"
    )
    print(f"  {'case':<8} {'ns/call':>10} {'marshal share':>16}")
    for name, stats in report.items():
        share = f"+{stats['marshal_ns']:.0f}" if name != "base" else "baseline"
        print(f"  {name:<8} {stats['ns_per_call']:>10.1f} {share:>16}")
    print(f"  baseline (no payload, seam only): {base:.1f} ns/call")
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps({"calls": args.calls, "repeats": args.repeats, "cases": report}, indent=1) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
