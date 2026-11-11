"""The pyscriptc_ndarray perf receipt (issue #496, M1 acceptance).

The compiled array arithmetic must beat CPython's numpy on the same
operation, by a recorded factor, on a workload of at least 1M elements,
and a CPython pure-Python list loop is the floor the comparison must
also clear.

The workload: 1M float64 elements, ten iterations of an elementwise
multiply and an add plus a whole-array sum. The compiled program runs
the pyscriptc_ndarray pack; the CPython lanes run real numpy and a
plain list[float] loop on the same machine.

    uv run python -m benchmarks.ndarray.receipt

Prints the three walls and the factors, and exits 1 when the compiled
program does not clear both bars. The numbers are machine-dependent;
the receipt records the run's numbers in the milestone description.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

COMPILED_SRC = """import pyscriptc_ndarray as np


def run() -> float:
    a = np.arange(0.0, 1000000.0, 1.0)
    b = a * 1.5
    c = b + a
    total = 0.0
    i = 0
    while i < 10:
        c = c * 1.0000001
        total = total + c.sum()
        i = i + 1
    return total


print(run())
"""

NUMPY_SRC = """import numpy as np

a = np.arange(0.0, 1000000.0, 1.0)
b = a * 1.5
c = b + a
total = 0.0
for i in range(10):
    c = c * 1.0000001
    total = total + c.sum()
print(total)
"""

LIST_SRC = """def run() -> float:
    a = [float(i) for i in range(1000000)]
    c = [x * 1.5 + x for x in a]
    total = 0.0
    for i in range(10):
        c = [x * 1.0000001 for x in c]
        total = total + sum(c)
    return total


print(run())
"""


def _wall(command: list[str], cwd: Path) -> float:
    start = time.perf_counter()
    subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)
    return time.perf_counter() - start


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        source = work / "bench.py"
        source.write_text(COMPILED_SRC, encoding="utf-8")
        binary = work / "bench"
        subprocess.run(
            [sys.executable, "-m", "pyscriptc", "compile", str(source), "--emit", "bin", "-o", str(binary)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        compiled = min(_wall([str(binary)], work) for _ in range(3))

        numpy_py = work / "bench_numpy.py"
        numpy_py.write_text(NUMPY_SRC, encoding="utf-8")
        numpy = min(_wall([sys.executable, str(numpy_py)], work) for _ in range(3))

        list_py = work / "bench_list.py"
        list_py.write_text(LIST_SRC, encoding="utf-8")
        floor = min(_wall([sys.executable, str(list_py)], work) for _ in range(3))

    print(f"compiled: {compiled:.3f}s  numpy: {numpy:.3f}s  list floor: {floor:.3f}s")
    print(f"vs numpy: {numpy / compiled:.2f}x faster; vs list floor: {floor / compiled:.2f}x faster")
    if compiled >= numpy:
        print("FAIL: the compiled program does not beat numpy", file=sys.stderr)
        return 1
    if compiled >= floor:
        print("FAIL: the compiled program does not clear the list floor", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
