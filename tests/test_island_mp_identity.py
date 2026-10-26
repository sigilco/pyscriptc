"""Island `sys.executable` and seed-module registration (issues #688, #689).

macOS-safe tests only compile and run island binaries that touch no
multiprocessing sync primitive (the host POSIX semaphore pool is exhausted);
the Process/Pool end-to-end cases are `linux_container` and run through
`HEAVY=1 docker/run.sh`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

EXEC_PROBE = "import sys\nprint(sys.executable)\n"

PICKLE_PROBE = """\
import pickle

def square(x: int) -> int:
    return x * x

if __name__ == "__main__":
    data = pickle.dumps(square)
    restored = pickle.loads(data)
    print(restored(7), square.__module__)
"""

MP_PROCESS_QUEUE = """\
import multiprocessing as mp

def producer(q, base):
    q.put([base, base + 10, base + 20])

if __name__ == "__main__":
    ctx = mp.get_context("fork")
    q = ctx.Queue()
    p = ctx.Process(target=producer, args=(q, 0))
    p.start()
    print(q.get())
    p.join()
"""

MP_POOL = """\
import multiprocessing as mp

def square(x):
    return x * x

if __name__ == "__main__":
    ctx = mp.get_context("fork")
    with ctx.Pool(2) as pool:
        print(pool.map(square, [1, 2, 3, 4]))
"""


def _cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "pyscriptc", *args], cwd=ROOT, capture_output=True, text=True)


def _compile_binary(tmp_path: Path, source: str, name: str) -> Path:
    src = tmp_path / name
    src.write_text(source, encoding="utf-8")
    binary = tmp_path / f"{name}_bin"
    comp = _cli("compile", str(src), "--fallback=cpython", "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr
    return binary


def test_island_sys_executable_matches_cpython(tmp_path: Path) -> None:
    """The two-line probe from #688: `sys.executable` is the real interpreter."""
    binary = _compile_binary(tmp_path, EXEC_PROBE, "exe_probe.py")
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
    want = subprocess.run([sys.executable, "-c", EXEC_PROBE], capture_output=True, text=True, timeout=60)
    assert got.returncode == want.returncode == 0, got.stderr
    assert got.stdout.strip() != ""
    assert got.stdout == want.stdout


def test_island_pickle_round_trip_of_module_function(tmp_path: Path) -> None:
    """The seed namespace registers in sys.modules, so island functions pickle."""
    binary = _compile_binary(tmp_path, PICKLE_PROBE, "pickle_fn.py")
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
    want = subprocess.run([sys.executable, "-c", PICKLE_PROBE], capture_output=True, text=True, timeout=60)
    assert got.returncode == want.returncode == 0, got.stderr
    assert got.stdout == want.stdout
    assert got.stdout.startswith("49 ")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_island_process_queue_matches_cpython(tmp_path: Path) -> None:
    """Process + Queue under the fork context prints like CPython (#689)."""
    binary = _compile_binary(tmp_path, MP_PROCESS_QUEUE, "mp_queue.py")
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120)
    want = subprocess.run([sys.executable, "-c", MP_PROCESS_QUEUE], capture_output=True, text=True, timeout=120)
    assert got.returncode == want.returncode == 0, (got.returncode, got.stdout, got.stderr)
    assert got.stdout == want.stdout == "[0, 10, 20]\n"


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_island_fork_pool_matches_cpython(tmp_path: Path) -> None:
    """Pool(fork).map over an island function matches CPython (#688 + #689)."""
    binary = _compile_binary(tmp_path, MP_POOL, "mp_pool_island.py")
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120)
    want = subprocess.run([sys.executable, "-c", MP_POOL], capture_output=True, text=True, timeout=120)
    assert got.returncode == want.returncode == 0, (got.returncode, got.stdout, got.stderr)
    assert got.stdout == want.stdout == "[1, 4, 9, 16]\n"
