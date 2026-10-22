"""Threading phase 2: native Thread, Event, Condition and Semaphore.

The design doc (2026-10-21-async-threads-design.md, P2b) scopes the surface:
threads run on real pthreads, the wait primitives are runtime records with
real mutexes and condvars, and the memory model is SC-DRF (the design doc,
section 4): synchronization through the threading primitives is a full
barrier pair, races on containers stay undefined behavior.

Every oracle case runs the same source under CPython and under the compiled
binary and compares the output. Deterministic ordering comes from join() and
Event handshakes, never from thread scheduling.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_THREAD_START_SOURCE = """
import threading

done: threading.Event = threading.Event()
sent: threading.Semaphore = threading.Semaphore(value=0)

def work(n: int) -> None:
    if n == 41:
        sent.release()
    done.set()

def checklist() -> int:
    t: threading.Thread = threading.Thread(target=work, args=(41,))
    t.start()
    ok: bool = sent.acquire()
    done.wait()
    t.join()
    if t.is_alive():
        return 1
    if not ok:
        return 2
    if not t.name.startswith("Thread"):
        return 3
    return 0
"""

_THREAD_JOIN_SOURCE = """
import threading

def spin(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + i
        i = i + 1
    return total

def checklist() -> int:
    t: threading.Thread = threading.Thread(target=spin, args=(1000,))
    t.start()
    t.join()
    return 0 if not t.is_alive() else 1
"""

_THREAD_NAME_DAEMON_SOURCE = """
import threading

done: threading.Event = threading.Event()

def work() -> None:
    done.set()

def checklist() -> int:
    t: threading.Thread = threading.Thread(target=work, args=(), name="worker", daemon=True)
    t.start()
    done.wait()
    t.join()
    if t.name != "worker":
        return 1
    if t.daemon is not True:
        return 2
    return 0
"""

_EVENT_SOURCE = """
import threading

ev: threading.Event = threading.Event()

def checklist() -> int:
    if ev.is_set():
        return 1
    if ev.wait(timeout=0.05):
        return 2
    ev.set()
    if not ev.is_set():
        return 3
    if not ev.wait(timeout=0.05):
        return 4
    ev.clear()
    if ev.is_set():
        return 5
    return 0
"""

_EVENT_ACROSS_THREADS_SOURCE = """
import threading

ev: threading.Event = threading.Event()

def setter() -> None:
    ev.set()

def checklist() -> int:
    t: threading.Thread = threading.Thread(target=setter, args=())
    t.start()
    ok: bool = ev.wait()
    t.join()
    return 0 if ok and ev.is_set() else 1
"""

_CONDITION_SOURCE = """
import threading

cond: threading.Condition = threading.Condition()
ready: int = 0

def checklist() -> int:
    cond.acquire()
    cond.release()
    got: bool = cond.acquire()
    cond.release()
    return 0 if got else 1
"""

_CONDITION_WAIT_TIMEOUT_SOURCE = """
import threading

cond: threading.Condition = threading.Condition()

def checklist() -> int:
    cond.acquire()
    woke: bool = cond.wait(timeout=0.05)
    cond.release()
    return 0 if woke is False else 1
"""

_SEMAPHORE_SOURCE = """
import threading

sem: threading.Semaphore = threading.Semaphore(value=2)

def checklist() -> int:
    if not sem.acquire(blocking=False):
        return 1
    if not sem.acquire(blocking=False):
        return 2
    if sem.acquire(blocking=False):
        return 3
    sem.release()
    if not sem.acquire(blocking=False):
        return 4
    sem.release()
    sem.release()
    return 0
"""

_SEMAPHORE_DEFAULT_SOURCE = """
import threading

sem: threading.Semaphore = threading.Semaphore()

def checklist() -> int:
    if sem._value != 1:
        return 1
    sem.acquire()
    sem.release()
    return 0
"""

_SEMAPHORE_ACROSS_THREADS_SOURCE = """
import threading

sem: threading.Semaphore = threading.Semaphore(value=0)
done: threading.Event = threading.Event()

def producer() -> None:
    sem.release()
    sem.release()
    done.set()

def checklist() -> int:
    t: threading.Thread = threading.Thread(target=producer, args=())
    t.start()
    first: bool = sem.acquire()
    second: bool = sem.acquire()
    done.wait()
    t.join()
    return 0 if first and second else 1
"""

_FROM_IMPORT_SOURCE = """
from threading import Event, Semaphore, Thread

ev: Event = Event()

def work() -> None:
    ev.set()

def checklist() -> int:
    t: Thread = Thread(target=work, args=())
    t.start()
    ok: bool = ev.wait()
    t.join()
    sem: Semaphore = Semaphore()
    if sem._value != 1:
        return 1
    return 0 if ok else 2
"""

_JOIN_BEFORE_START_SOURCE = """
import threading

def work() -> None:
    pass

def checklist() -> int:
    t: threading.Thread = threading.Thread(target=work, args=())
    t.join()
    return 0
"""

_DOUBLE_START_SOURCE = """
import threading

done: threading.Event = threading.Event()

def work() -> None:
    done.set()

def checklist() -> int:
    t: threading.Thread = threading.Thread(target=work, args=())
    t.start()
    done.wait()
    t.join()
    t.start()
    return 0
"""

_SEM_NEGATIVE_SOURCE = """
import threading

def checklist() -> int:
    sem: threading.Semaphore = threading.Semaphore(value=-1)
    return 0
"""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_thread_start_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "start.py", _THREAD_START_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "checklist")
    assert got.stdout == "0\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_thread_join_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "join.py", _THREAD_JOIN_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "checklist")
    assert got.stdout == "0\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_thread_name_and_daemon_match_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "name.py", _THREAD_NAME_DAEMON_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "checklist")
    assert got.stdout == "0\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_event_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "event.py", _EVENT_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "checklist")
    assert got.stdout == "0\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_event_handshake_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "handshake.py", _EVENT_ACROSS_THREADS_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "checklist")
    assert got.stdout == "0\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_condition_acquire_release_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "condition.py", _CONDITION_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "checklist")
    assert got.stdout == "0\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_condition_wait_timeout_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "cond_wait.py", _CONDITION_WAIT_TIMEOUT_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "checklist")
    assert got.stdout == "0\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_semaphore_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "semaphore.py", _SEMAPHORE_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "checklist")
    assert got.stdout == "0\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_semaphore_default_value_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "sem_default.py", _SEMAPHORE_DEFAULT_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "checklist")
    assert got.stdout == "0\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_semaphore_producer_consumer_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "prodcon.py", _SEMAPHORE_ACROSS_THREADS_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "checklist")
    assert got.stdout == "0\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_from_import_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "from_import.py", _FROM_IMPORT_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "checklist")
    assert got.stdout == "0\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_join_before_start_raises(tmp_path: Path) -> None:
    """CPython raises RuntimeError; the compiled program raises the same class."""
    path = write_case(tmp_path, "join_early.py", _JOIN_BEFORE_START_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode != 0
    assert "RuntimeError" in got.stderr, got.stderr


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_double_start_raises(tmp_path: Path) -> None:
    """CPython raises RuntimeError; the compiled program raises the same class."""
    path = write_case(tmp_path, "double_start.py", _DOUBLE_START_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode != 0
    assert "RuntimeError" in got.stderr, got.stderr


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_negative_semaphore_raises(tmp_path: Path) -> None:
    """CPython raises ValueError; the compiled program raises the same class."""
    path = write_case(tmp_path, "sem_neg.py", _SEM_NEGATIVE_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode != 0
    assert "ValueError" in got.stderr, got.stderr


def test_unsupported_surface_stays_fenced(tmp_path: Path) -> None:
    """Phase 2 has no local, no kwargs, no group: the fences keep it honest."""
    cases = [
        ("local.py", "threading.local", "threading.local is not in the lowered subset of threading"),
        ("group.py", 'threading.Thread(group=None, target=probe_target, args=(1,))', "threading.Thread ignores group"),
        (
            "kwargs.py",
            "threading.Thread(target=probe_target, kwargs={'a': 1})",
            "threading.Thread kwargs are not lowered",
        ),
        (
            "noargs.py",
            "threading.Thread()",
            "threading.Thread needs a target",
        ),
    ]
    for name, expr, text in cases:
        source = f"import threading\ndef probe_target(a: int = 0) -> None:\n    pass\ndef probe() -> int:\n    {expr}\n    return 1\n"
        path = write_case(tmp_path, name, source)
        got = run_cli(
            "compile",
            str(path),
            "--skip-check",
            "--emit",
            "llvm",
            "-o",
            str(tmp_path / f"{name}.ll"),
        )
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"
