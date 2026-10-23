"""Threading stress: the shared-state receipts.

The P2b exit of issue #172 asks for 8 threads doing 10^6 locked increments
with an exact count. The runtime keeps one global slot table and one MT19937
stream for the whole process, so a second thread can reach both; one mutex
per table keeps them coherent. This suite pins the exact-count receipt and
the global audit.

Every oracle case runs the same source under CPython and the compiled binary
and compares stdout. Determinism comes from join(), never from scheduling.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, write_case

_THREADS_COUNT_SOURCE = """
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
    a: threading.Thread = threading.Thread(target=bump, args=(1000000,))
    b: threading.Thread = threading.Thread(target=bump, args=(1000000,))
    c: threading.Thread = threading.Thread(target=bump, args=(1000000,))
    d: threading.Thread = threading.Thread(target=bump, args=(1000000,))
    e: threading.Thread = threading.Thread(target=bump, args=(1000000,))
    f: threading.Thread = threading.Thread(target=bump, args=(1000000,))
    g: threading.Thread = threading.Thread(target=bump, args=(1000000,))
    h: threading.Thread = threading.Thread(target=bump, args=(1000000,))
    a.start()
    b.start()
    c.start()
    d.start()
    e.start()
    f.start()
    g.start()
    h.start()
    a.join()
    b.join()
    c.join()
    d.join()
    e.join()
    f.join()
    g.join()
    h.join()
    if count != 8000000:
        return 1
    return 0
"""

_THREADS_GLOBAL_STR_SOURCE = """
import threading

seen = ""

def put() -> None:
    global seen
    seen = "from-thread"

def checklist() -> int:
    a: threading.Thread = threading.Thread(target=put, args=())
    a.start()
    a.join()
    if seen != "from-thread":
        return 1
    return 0
"""

_THREADS_RAND_SOURCE = """
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


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_eight_threads_million_locked_increments_exact(tmp_path: Path) -> None:
    """8 threads, 10^6 locked increments each, the count is exact."""
    path = write_case(tmp_path, "stress.py", _THREADS_COUNT_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "checklist")
    assert got.stdout == "0\n"


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_global_str_write_from_thread_visible_after_join(tmp_path: Path) -> None:
    """A module-level str slot written in a thread reads back after join."""
    path = write_case(tmp_path, "gstr.py", _THREADS_GLOBAL_STR_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "checklist")
    assert got.stdout == "0\n"


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_module_random_stream_survives_threads(tmp_path: Path) -> None:
    """The module-level random stream stays coherent across threads."""
    path = write_case(tmp_path, "rand.py", _THREADS_RAND_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "checklist")
    assert got.stdout == "0\n"
