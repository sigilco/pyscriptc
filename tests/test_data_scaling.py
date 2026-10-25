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
import shutil
import subprocess
import sys
import sysconfig
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


# The set index doubles its probe table (ps_seq_index_add in
# src/pyscriptc/runtime/ps_rt.c). Before that it re-derived the table size
# from the length on every grow, which landed the new table at exactly load
# one half, so the very next push rebuilt it again: a doubling cost two full
# rehashes, and one more element past a power-of-two length paid for both.
# That element now costs a push.
#
# The guard is deterministic: a tiny C probe declares the PsSeq layout from
# ps_rt.h, fills an int-indexed set to a length just below a power of two so
# the probe table sits at exactly load one half, then pushes one element and
# asserts the table doubled on that single push. The pre-#646 shape re-derived
# the table size from the length with `count_for`, which rebuilt to the same
# slot count (a wasted full rehash) at exactly this point and only doubled on
# the following push. No wall clock, so runner noise cannot flake it (#686).
REBUILD_SIZE = 1 << 12
REBUILD_PROBE = """#include <stdint.h>
#include <stdio.h>
#include <ps_rt.h>

int main(void) {
    PsSeq *s = ps_seq_new(8, 0);
    for (int64_t i = 0; i < REBUILD_SIZE - 1; i++) {
        int64_t v = i;
        ps_seq_push(s, &v);
    }
    /* The containment test builds the index: at length REBUILD_SIZE - 1 the
       probe table holds REBUILD_SIZE slots, so it sits at exactly load one
       half. */
    if (ps_seq_has_i64(s, 1) != 1) {
        return 2;
    }
    if (s->idx_count != REBUILD_SIZE * 2) {
        fprintf(stderr, "index built at %lld slots, expected %d\\n", (long long)s->idx_count, REBUILD_SIZE * 2);
        return 3;
    }
    int64_t before = s->idx_count;
    int64_t one = 1;
    ps_seq_push(s, &one);
    int64_t after = s->idx_count;
    printf("%lld %lld\\n", (long long)before, (long long)after);
    /* Doubling: that one push grows the table to exactly twice its slot
       count. A rebuild from the length keeps the same slot count (a wasted
       full rehash) and doubles only on the next push. */
    return after == before * 2 ? 0 : 1;
}
"""


def test_set_push_past_a_power_of_two_does_not_rehash(tmp_path: Path) -> None:
    probe_c = tmp_path / "rebuild_probe.c"
    probe_c.write_text(REBUILD_PROBE.replace("REBUILD_SIZE", str(REBUILD_SIZE)), encoding="utf-8")
    probe = tmp_path / "rebuild_probe"
    cc = shutil.which("clang") or shutil.which("cc")
    assert cc is not None, "no C compiler on PATH"
    runtime = ROOT / "src" / "pyscriptc" / "runtime"
    includes = [f"-I{runtime}", f"-I{sysconfig.get_path('include')}"]
    # The island and mix units embed the host CPython and are not part of
    # this probe; the driver drops them for non-fallback builds too.
    sources = sorted(p for p in runtime.glob("*.c") if p.name not in ("ps_island.c", "ps_mix.c"))
    built = subprocess.run(
        [cc, "-O1", *includes, str(probe_c), *(str(p) for p in sources), "-o", str(probe)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert built.returncode == 0, built.stderr
    run = subprocess.run([str(probe)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, (
        f"push past {REBUILD_SIZE} did not double the probe table "
        f"(exit {run.returncode}, out {run.stdout!r}, err {run.stderr!r}); "
        "the index table is being rebuilt instead of doubled"
    )
