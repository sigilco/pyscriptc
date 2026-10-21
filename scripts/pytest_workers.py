"""Pick a pytest-xdist worker count bounded by available CPU and memory.

workers = clamp(min(cpus, floor((mem_gb - RESERVE_GB) / PER_WORKER_GB)), 1, CAP)

Usage: `uv run pytest -n auto` (conftest hook) or `-n $(python scripts/pytest_workers.py)`.
Override with PYTEST_WORKERS=<int>. `--explain` prints the inputs.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Named thresholds. Measured on the CI selection (see
# .agents/plans/2026-10-21-xdist-workers-and-split.md): a worker plus its
# clang/zig children peaks near 0.1 GB of tree RSS each (largest single process 0.2 GB); 0.15 GB leaves headroom
# for the heaviest compiles and for page cache.
PER_WORKER_GB = 0.15
RESERVE_GB = 0.25  # left for the OS, uv, editors
CAP = 6  # measured knee on an 8-core Mac; CI runners have fewer cores anyway
GB = 1024**3


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def cpus() -> int:
    n = os.process_cpu_count() if hasattr(os, "process_cpu_count") else None
    n = n or os.cpu_count() or 1
    quota = _read("/sys/fs/cgroup/cpu.max")  # cgroup v2: "<quota|max> <period>"
    if quota and not quota.startswith("max"):
        q, p = quota.split()[:2]
        n = min(n, max(1, int(q) // int(p)))
    return n


def mem_gb() -> float | None:
    """Memory available to this process in GB, or None if unknown."""
    limits: list[float] = []
    meminfo = _read("/proc/meminfo")
    if meminfo:
        for line in meminfo.splitlines():
            if line.startswith("MemAvailable:"):
                limits.append(int(line.split()[1]) * 1024 / GB)
        cg = _read("/sys/fs/cgroup/memory.max")
        if cg and cg != "max":
            used = int(_read("/sys/fs/cgroup/memory.current") or 0)
            limits.append((int(cg) - used) / GB)
    elif sys.platform == "darwin":
        try:
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
            page = int(out.split("page size of")[1].split("bytes")[0])
            pages = 0
            for line in out.splitlines():
                if line.split(":")[0] in {"Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"}:
                    pages += int(line.split(":")[1].strip().rstrip("."))
            limits.append(pages * page / GB)
        except OSError, ValueError, IndexError, subprocess.SubprocessError:
            pass
    return min(limits) if limits else None


def workers() -> int:
    forced = os.environ.get("PYTEST_WORKERS")
    if forced:
        return max(1, int(forced))
    n = cpus()
    m = mem_gb()
    if m is not None:
        n = min(n, int((m - RESERVE_GB) // PER_WORKER_GB))
    return max(1, min(n, CAP))


if __name__ == "__main__":
    if "--explain" in sys.argv:
        print(
            f"cpus={cpus()} mem_gb={mem_gb()} per_worker={PER_WORKER_GB} reserve={RESERVE_GB} cap={CAP}",
            file=sys.stderr,
        )
    print(workers())
