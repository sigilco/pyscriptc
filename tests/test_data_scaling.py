"""Per-PR complexity guards on the data operations (#388, workstream B3).

Each case compiles one workload and runs it at two sizes, N and 4N, on the
same machine seconds apart. The exponent of the ratio bounds the growth
class: an O(n) shape must stay near 1, so an accidental O(n^2) lands here
as a red test instead of a shipped regression. The exponents ride on the
timing ratio of the two sizes only; no assertion depends on machine speed
except the generous absolute floor that keeps a size too small to time
from producing noise.

The dict and set workloads drove the #382 index. On master the dict case
measured exp 1.9 (quadratic) and failed this bound; the index brings it
under 1.4.
"""

from __future__ import annotations

import math
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Every case: (name, source, size, exponent bound). The bound sits well
# above the linear 1.0 but far below the quadratic 2.0, and 0.35 above the
# measured post-#382 value absorbs runner noise. The list case is the
# control: an append is amortized O(1), so it fails if the runner itself
# is distorting timings.
CASES = [
    (
        "dict_grow",
        """def bench(n: int) -> int:
    d: dict[int, int] = {}
    i: int = 0
    while i < n:
        d[i] = i * 2
        i = i + 1
    total: int = 0
    for k in d.keys():
        total = total + k
    return total + len(d)
""",
        20000,
        1.4,
    ),
    (
        "set_grow",
        """def bench(n: int) -> int:
    s: set[int] = set()
    i: int = 0
    while i < n:
        s.add(i)
        i = i + 1
    total: int = 0
    for item in s:
        total = total + item
    return total + len(s)
""",
        20000,
        1.4,
    ),
    (
        "dict_lookup",
        """def bench(n: int) -> int:
    d: dict[int, int] = {}
    i: int = 0
    while i < n:
        d[i] = i
        i = i + 1
    hits: int = 0
    j: int = 0
    while j < n:
        if j in d:
            hits = hits + 1
        j = j + 1
    return hits + len(d)
""",
        20000,
        1.4,
    ),
    (
        "list_append",
        """def bench(n: int) -> int:
    xs: list[int] = []
    i: int = 0
    while i < n:
        xs.append(i)
        i = i + 1
    total: int = 0
    for x in xs:
        total = total + x
    return total + len(xs)
""",
        50000,
        1.4,
    ),
    (
        "json_loads",
        """import json

def bench(n: int) -> int:
    d: dict[str, int] = {}
    i: int = 0
    while i < n:
        d[str(i)] = i
        i = i + 1
    raw: str = json.dumps(d)
    parsed: dict[str, int] = json.loads(raw)
    return len(parsed)
""",
        5000,
        1.4,
    ),
    (
        "copyfile",
        """import os
import shutil
import tempfile

def bench(n: int) -> int:
    d: str = tempfile.mkdtemp()
    src: str = os.path.join(d, "src.bin")
    dst: str = os.path.join(d, "dst.bin")
    with open(src, "wb") as handle:
        handle.write(b"a" * n)
    shutil.copyfile(src, dst)
    size: int = os.path.getsize(dst)
    shutil.rmtree(d)
    return size
""",
        200000,
        1.4,
    ),
    (
        "sha256_blocks",
        """import hashlib

def bench(n: int) -> int:
    data: bytes = b"x" * (n * 64)
    digest: str = hashlib.sha256(data).hexdigest()
    return len(digest)
""",
        20000,
        1.4,
    ),
]

FLOOR_MS = 2.0  # below this the size is too small to time at all


def _timed(binary: Path) -> float:
    """The median wall time of five runs, in milliseconds."""
    samples: list[float] = []
    for _ in range(5):
        start = time.perf_counter()
        proc = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
        elapsed = (time.perf_counter() - start) * 1000.0
        assert proc.returncode == 0, proc.stderr + proc.stdout
        samples.append(elapsed)
    samples.sort()
    return samples[len(samples) // 2]


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_data_operation_stays_linear(case, tmp_path: Path) -> None:
    name, source, size, bound = case
    src = tmp_path / f"{name}.py"
    src.write_text(source, encoding="utf-8")
    times: dict[int, float] = {}
    for n in (size, size * 4):
        binary = tmp_path / f"{name}-{n}"
        compiled = subprocess.run(
            [
                sys.executable,
                "-m",
                "pyscriptc",
                "compile",
                str(src),
                "--emit",
                "bin",
                "--call",
                "bench",
                "--args",
                f"[{n}]",
                "-o",
                str(binary),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert compiled.returncode == 0, compiled.stderr + compiled.stdout
        times[n] = _timed(binary)
    exponent = math.log(times[size * 4] / times[size]) / math.log(4)
    assert times[size] > FLOOR_MS or times[size * 4] > FLOOR_MS, times
    assert exponent < bound, f"{name}: exponent {exponent:.2f} >= {bound} (times {times})"
