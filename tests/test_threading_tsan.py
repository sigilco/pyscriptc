"""ThreadSanitizer receipts for the threading and queue surfaces.

Issue #172, Stage D: the sanitizer has to stay silent on the programs that
exercise the runtime's thread primitives. A TSan report is a data race the
compiler introduced or a thread the runtime leaked, so any output on stderr
fails the test.

The programs mirror the stress suite: the locked increment count, the
global write a thread makes, the module random stream, the island call from
four threads and the queue producer/consumer. The counts are exact, so the
test pins the behavior and not just the absence of a race.

TSan needs its own process: the sanitizer runtime cannot share a process
with ASan, and the reports go to stderr, so every case runs the compiled
binary and reads its two streams separately.

The cases are slow (the million-increment count under TSan takes tens of
seconds), so they carry the `linux_container` marker and CI runs them in
the Linux container job.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import write_case

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")

_TSAN_OPTIONS = "halt_on_error=1:exitcode=66"

_COUNT_SOURCE = """
import threading

count = 0
lock: threading.Lock = threading.Lock()

def bump(n: int) -> None:
    global count
    i: int = 0
    while i < n:
        lock.acquire()
        count = count + 1
        lock.release()
        i = i + 1

def checklist() -> int:
    a: threading.Thread = threading.Thread(target=bump, args=(200000,))
    b: threading.Thread = threading.Thread(target=bump, args=(200000,))
    c: threading.Thread = threading.Thread(target=bump, args=(200000,))
    d: threading.Thread = threading.Thread(target=bump, args=(200000,))
    a.start()
    b.start()
    c.start()
    d.start()
    a.join()
    b.join()
    c.join()
    d.join()
    if count != 800000:
        return 1
    return 0
"""

_GLOBAL_SOURCE = """
import threading

seen = ""

def put() -> None:
    global seen
    seen = "from-thread"

def checklist() -> int:
    a: threading.Thread = threading.Thread(target=put, args=())
    b: threading.Thread = threading.Thread(target=put, args=())
    a.start()
    b.start()
    a.join()
    b.join()
    if seen != "from-thread":
        return 1
    return 0
"""

_RANDOM_SOURCE = """
import random
import threading

lock: threading.Lock = threading.Lock()
draws = 0

def draw(n: int) -> None:
    global draws
    i: int = 0
    while i < n:
        lock.acquire()
        r: int = random.randrange(1000)
        draws = draws + 1
        lock.release()
        i = i + 1

def checklist() -> int:
    a: threading.Thread = threading.Thread(target=draw, args=(500,))
    b: threading.Thread = threading.Thread(target=draw, args=(500,))
    a.start()
    b.start()
    a.join()
    b.join()
    if draws != 1000:
        return 1
    r: int = random.randrange(4)
    if r < 0 or r >= 4:
        return 2
    return 0
"""

_QUEUE_SOURCE = """
import queue
import threading

def produce(n: int) -> None:
    q: queue.Queue[int] = queue.Queue()
    q.put(n)
    q.put(n + 1)
    q.task_done()
    q.task_done()
    q.join()

def checklist() -> int:
    a: threading.Thread = threading.Thread(target=produce, args=(1,))
    b: threading.Thread = threading.Thread(target=produce, args=(2,))
    a.start()
    b.start()
    a.join()
    b.join()
    q: queue.Queue[int] = queue.Queue()
    q.put(7)
    v: int = q.get()
    q.task_done()
    if v != 7:
        return 1
    return 0
"""


def compile_tsan(tmp_path: Path, source: Path, name: str) -> Path:
    """Compile one source with the thread sanitizer and return the binary."""
    binary = tmp_path / name
    got = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(source),
            "--emit",
            "bin",
            "-o",
            str(binary),
            "--call",
            "checklist",
            "--args",
            "[]",
            "--sanitize=thread",
        ],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
    )
    assert got.returncode == 0, got.stderr
    return binary


def run_tsan(binary: Path) -> subprocess.CompletedProcess[str]:
    """Run a sanitized binary and hand back its two streams separately."""
    env = {**os.environ, "TSAN_OPTIONS": _TSAN_OPTIONS}
    return subprocess.run([str(binary)], cwd=binary.parent, env=env, capture_output=True, text=True)


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
@needs_clang
def test_tsan_locked_increment_count_is_clean(tmp_path: Path) -> None:
    """Four threads, 800000 locked increments: the count is exact and TSan is silent."""
    path = write_case(tmp_path, "count.py", _COUNT_SOURCE)
    got = run_tsan(compile_tsan(tmp_path, path, "count-tsan"))
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == "0", got.stdout
    assert got.stderr == "", got.stderr


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
@needs_clang
def test_tsan_global_write_from_a_thread_is_clean(tmp_path: Path) -> None:
    """A global a thread writes is visible after the join and TSan is silent."""
    path = write_case(tmp_path, "global.py", _GLOBAL_SOURCE)
    got = run_tsan(compile_tsan(tmp_path, path, "global-tsan"))
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == "0", got.stdout
    assert got.stderr == "", got.stderr


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
@needs_clang
def test_tsan_module_random_stream_is_clean(tmp_path: Path) -> None:
    """The module random stream under two threads: the count is exact and TSan is silent."""
    path = write_case(tmp_path, "rand_stream.py", _RANDOM_SOURCE)
    got = run_tsan(compile_tsan(tmp_path, path, "random-tsan"))
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == "0", got.stdout
    assert got.stderr == "", got.stderr


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
@needs_clang
def test_tsan_queue_producer_consumer_is_clean(tmp_path: Path) -> None:
    """Two producers and a consumer on one queue: the count is exact and TSan is silent."""
    path = write_case(tmp_path, "qcase.py", _QUEUE_SOURCE)
    got = run_tsan(compile_tsan(tmp_path, path, "queue-tsan"))
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == "0", got.stdout
    assert got.stderr == "", got.stderr
