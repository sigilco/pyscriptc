"""Island `sys.executable`, seed-module registration and exitfuncs (issues #679, #688, #689).

macOS-safe tests only compile and run island binaries that touch no
multiprocessing sync primitive (the host POSIX semaphore pool is exhausted);
the Pool/Queue end-to-end cases are `linux_container` and run through
`HEAVY=1 docker/run.sh`. A bare Process under the spawn/forkserver contexts
needs no named semaphore, so the #690 cases below run on the host.
"""

from __future__ import annotations

import os
import signal
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

# Issue #690: `sys.executable` is the binary, so spawn/forkserver children
# re-exec it as `binary -c PAYLOAD`. The island seed answers the re-import of
# `__main__` from the embedded source, never a file on disk.
MP_CHILD_PROCESS = """\
import multiprocessing as mp

def worker(n):
    print(f"child:{{n * n}}", flush=True)

if __name__ == "__main__":
    ctx = mp.get_context({method!r})
    p = ctx.Process(target=worker, args=(7,))
    p.start()
    p.join()
    print(f"exit={{p.exitcode}}")
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

ATEXIT_PROBE = """\
import atexit

def first():
    print("first")

def second():
    print("second")

atexit.register(first)
atexit.register(second)
atexit.register(lambda: print("lambda bye"))
print("body done")
"""

ATEXIT_SYSEXIT_PROBE = """\
import atexit

atexit.register(lambda: print("atexit ran"))
raise SystemExit(3)
"""


def _cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "pyscriptc", *args], cwd=ROOT, capture_output=True, text=True)


def _compile_binary(tmp_path: Path, source: str, name: str) -> Path:
    src = tmp_path / name
    src.write_text(source, encoding="utf-8")
    binary = tmp_path / f"{name}_bin"
    comp = _cli(
        "compile",
        str(src),
        "--fallback=cpython",
        "--allow-host-libpython",
        "--emit",
        "bin",
        "-o",
        str(binary),
        cwd=tmp_path,
    )
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


def test_island_atexit_handlers_run_at_exit(tmp_path: Path) -> None:
    """Island `atexit.register` handlers run when the binary exits (#679)."""
    binary = _compile_binary(tmp_path, ATEXIT_PROBE, "atexit_probe.py")
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
    want = subprocess.run([sys.executable, "-c", ATEXIT_PROBE], capture_output=True, text=True, timeout=60)
    assert got.returncode == want.returncode == 0, got.stderr
    assert got.stdout == want.stdout
    assert got.stdout.splitlines() == ["body done", "lambda bye", "second", "first"]


def test_island_atexit_handlers_run_before_system_exit(tmp_path: Path) -> None:
    """A `raise SystemExit(3)` still delivers the island exitfuncs (#679)."""
    binary = _compile_binary(tmp_path, ATEXIT_SYSEXIT_PROBE, "atexit_sysexit.py")
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
    want = subprocess.run([sys.executable, "-c", ATEXIT_SYSEXIT_PROBE], capture_output=True, text=True, timeout=60)
    assert got.returncode == want.returncode == 3, (got.returncode, got.stdout, got.stderr)
    assert got.stdout == want.stdout == "atexit ran\n"


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


def _run_grouped(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Run `args` in its own process group so a runaway spawn tree dies whole.

    The #690 children are re-execs of the binary; a regression that lets them
    reach the program would recurse, so the timeout kill takes the group.
    """

    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        out, err = proc.communicate()
        raise
    return subprocess.CompletedProcess(args, proc.returncode, out, err)


@pytest.mark.parametrize("method", ["spawn", "forkserver"])
def test_island_child_process_matches_cpython(tmp_path: Path, method: str) -> None:
    """`binary -c spawn_main` re-imports `__main__` from the embedded seed.

    Issue #690: the spawn and forkserver children exec `sys.executable`
    (the binary); the source file is deleted before the run, so the child
    has nothing to re-import but the embedded copy.
    """
    source = MP_CHILD_PROCESS.format(method=method)
    binary = _compile_binary(tmp_path, source, f"mp_{method}.py")
    (tmp_path / f"mp_{method}.py").unlink()
    oracle = tmp_path / f"mp_{method}_oracle.py"
    oracle.write_text(source, encoding="utf-8")
    got = _run_grouped([str(binary)], timeout=120)
    want = subprocess.run([sys.executable, str(oracle)], capture_output=True, text=True, timeout=120)
    assert got.returncode == want.returncode == 0, (got.returncode, got.stdout, got.stderr)
    assert got.stdout == want.stdout == "child:49\nexit=0\n"


def test_island_dash_c_payload_runs_in_island(tmp_path: Path) -> None:
    """`binary -c PAYLOAD` runs the payload, not the program (#690)."""
    binary = _compile_binary(tmp_path, MP_CHILD_PROCESS.format(method="spawn"), "mp_dashc.py")
    got = subprocess.run(
        [str(binary), "-c", "import multiprocessing; print('payload-ran')"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert got.returncode == 0, got.stderr
    assert got.stdout == "payload-ran\n"


def test_island_dash_c_non_multiprocessing_runs_program(tmp_path: Path) -> None:
    """A `-c` that does not name multiprocessing stays a program argument."""
    source = "import multiprocessing as mp\nimport sys\nprint(sys.argv)\n"
    binary = _compile_binary(tmp_path, source, "mp_argc.py")
    got = subprocess.run([str(binary), "-c", "print(1)"], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0, got.stderr
    assert "'-c', 'print(1)'" in got.stdout


MP_CHILD_POOL = """\
import multiprocessing as mp

def square(x):
    return x * x

if __name__ == "__main__":
    ctx = mp.get_context({method!r})
    with ctx.Pool(2) as pool:
        print(pool.map(square, [1, 2, 3, 4]))
"""


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh - Pool needs named sems
@pytest.mark.parametrize("method", ["spawn", "forkserver"])
def test_island_child_pool_matches_cpython(tmp_path: Path, method: str) -> None:
    """Pool under spawn/forkserver: spawn_main plus the resource tracker.

    The tracker child is the same `binary -c PAYLOAD` shape (#690), so a
    Queue-carrying Pool covers both helper children end to end.
    """
    source = MP_CHILD_POOL.format(method=method)
    binary = _compile_binary(tmp_path, source, f"mp_pool_{method}.py")
    (tmp_path / f"mp_pool_{method}.py").unlink()
    oracle = tmp_path / f"mp_pool_{method}_oracle.py"
    oracle.write_text(source, encoding="utf-8")
    got = _run_grouped([str(binary)], timeout=180)
    want = subprocess.run([sys.executable, str(oracle)], capture_output=True, text=True, timeout=180)
    assert got.returncode == want.returncode == 0, (got.returncode, got.stdout, got.stderr)
    assert got.stdout == want.stdout == "[1, 4, 9, 16]\n"
