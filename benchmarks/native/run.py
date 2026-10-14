"""Time a Python loop beside a real .so, direct and through the island.

This is not the baseline ratio gate. It answers one question: if the Python
around a native library is compiled, and the .so stays native, does the
whole run get faster?
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from benchmarks.harness import (
    bootstrap_ratio,
    compile_call,
    cpython_stdout,
    measure_command,
    oracle_command,
    repo_root,
    time_command,
    verdict,
    versus_cpython,
)
from benchmarks.native.build import ensure

# name, file, call, n, route
WORK = (
    ("python-direct", "py_direct.py", "python_heavy", 200_000, "one C-ABI call after the loop"),
    ("python-island", "py_island.py", "python_heavy", 200_000, "one island call after the loop"),
    ("per-call-direct", "call_direct.py", "call_heavy", 20_000, "C-ABI call every iteration"),
    ("per-call-island", "call_island.py", "call_heavy", 20_000, "island call every iteration"),
    ("so-direct", "so_direct.py", "so_heavy", 2_000_000, "the loop is inside libmix.so"),
    ("so-island", "so_island.py", "so_heavy", 2_000_000, "one island call into that loop"),
)


def prepare() -> Path:
    lib, _ext = ensure()
    root = repo_root()
    native = root / "benchmarks" / "native"
    os.environ["PYSCRIPTC_NATIVEPROBE"] = "1"
    os.environ["PYSCRIPTC_MIX_SO"] = str(lib)
    previous = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = str(native) if not previous else str(native) + os.pathsep + previous
    return native


def main() -> int:
    runs = 5
    if len(sys.argv) > 1:
        runs = int(sys.argv[1])
    native = prepare()
    out = Path("/tmp/pyscriptc-native")
    out.mkdir(exist_ok=True)
    print(f"{'workload':16} {'time':>16} {'rss':>14}  native vs CPython")
    for name, filename, call, count, route in WORK:
        source = native / "workloads" / filename
        binary = out / name
        compiled = compile_call(source, call, [count], binary, skip_check=True)
        if compiled.returncode != 0:
            print(compiled.stderr, file=sys.stderr)
            return compiled.returncode
        oracle = cpython_stdout(source, call, [count])
        if oracle.returncode != 0 or oracle.stdout != _run_stdout(binary):
            print(f"{name}: stdout diverged", file=sys.stderr)
            print("native", _run_stdout(binary), file=sys.stderr)
            print("cpython", oracle.stdout, oracle.stderr, file=sys.stderr)
            return 1
        native_ms, native_rss = _samples([str(binary)], runs)
        cpython_ms, cpython_rss = _samples(oracle_command(source, call, [count]), runs)
        time_ratio = _median(native_ms) / _median(cpython_ms)
        rss_ratio = _median(native_rss) / max(_median(cpython_rss), 1)
        low, high = bootstrap_ratio(native_ms, cpython_ms)
        print(
            f"{name:16} {versus_cpython(time_ratio):>16} {versus_cpython(rss_ratio):>14}"
            f"  {_median(native_ms):7.1f} ms vs {_median(cpython_ms):7.1f} ms"
            f"  rss {_median(native_rss):.0f}/{_median(cpython_rss):.0f} KB"
            f"  {verdict(low, high)}  {route}"
        )
    return 0


def _run_stdout(binary: Path) -> str:
    _elapsed, completed = time_command([str(binary)])
    return completed.stdout


def _samples(command: list[str], runs: int) -> tuple[list[float], list[float]]:
    times: list[float] = []
    rss: list[float] = []
    for _ in range(runs):
        sample = measure_command(command)
        if sample.returncode != 0:
            raise SystemExit(sample.stderr)
        times.append(sample.elapsed_ms)
        rss.append(float(sample.rss_kb))
    return times, rss


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


if __name__ == "__main__":
    raise SystemExit(main())
