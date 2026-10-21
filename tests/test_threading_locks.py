"""Threading phase 1: native Lock, RLock, get_ident and the current thread.

The design doc (2026-10-21-async-threads-design.md, P2a) scopes phase 1 to
the primitives that need no second thread. The locks are real pthread mutexes,
so the acquire/release pair is a full barrier pair, but the compiled program
runs on one thread: `Thread`, `Condition`, `Event` and `Semaphore` stay
fenced.

Every oracle case runs the same source under CPython and under the compiled
binary and compares the output.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_LOCK_SOURCE = """
import threading

def checklist() -> int:
    lock: threading.Lock = threading.Lock()
    lock.acquire()
    if not lock.locked():
        return 1
    lock.release()
    if lock.locked():
        return 2
    if lock.acquire(blocking=False) is not True:
        return 3
    if lock.acquire(blocking=False):
        return 4
    lock.release()
    return 0
"""

_RLOCK_SOURCE = """
import threading

def checklist() -> int:
    rl: threading.RLock = threading.RLock()
    rl.acquire()
    rl.acquire()
    if not rl.locked():
        return 1
    if rl.acquire(blocking=False) is not True:
        return 2
    rl.release()
    if not rl.locked():
        return 3
    rl.release()
    rl.release()
    if rl.locked():
        return 4
    return 0
"""

_TIMEOUT_SOURCE = """
import threading

def checklist() -> int:
    lock: threading.Lock = threading.Lock()
    if lock.acquire(timeout=0.5) is not True:
        return 1
    lock.release()
    rl: threading.RLock = threading.RLock()
    if rl.acquire(timeout=1.0) is not True:
        return 2
    rl.release()
    return 0
"""

_IDENT_SOURCE = """
import threading

def checklist() -> int:
    ident: int = threading.get_ident()
    if ident <= 0:
        return 1
    name: str = threading.current_thread().name
    if name != "MainThread":
        return 2
    if threading.active_count() != 1:
        return 3
    return 0
"""

_FROM_IMPORT_SOURCE = """
from threading import Lock, RLock, get_ident

def checklist() -> int:
    lock: Lock = Lock()
    lock.acquire()
    lock.release()
    rl: RLock = RLock()
    rl.acquire()
    rl.release()
    ident: int = get_ident()
    if ident <= 0:
        return 1
    return 0
"""

_UNHELD_RELEASE_SOURCE = """
import threading

def checklist() -> int:
    lock: threading.Lock = threading.Lock()
    lock.release()
    return 0
"""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_lock_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "lock.py", _LOCK_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "checklist")
    assert got.stdout == "0\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_rlock_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "rlock.py", _RLOCK_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "checklist")
    assert got.stdout == "0\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_acquire_timeout_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "timeout.py", _TIMEOUT_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "checklist")
    assert got.stdout == "0\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_thread_identity_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "ident.py", _IDENT_SOURCE)
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
def test_release_of_an_unheld_lock_raises(tmp_path: Path) -> None:
    """CPython raises RuntimeError; the compiled program raises the same class."""
    path = write_case(tmp_path, "unheld.py", _UNHELD_RELEASE_SOURCE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode != 0
    assert "RuntimeError" in got.stderr, got.stderr


def test_thread_creation_stays_fenced(tmp_path: Path) -> None:
    """Phase 1 has no Thread: the fence keeps the surface honest."""
    cases = [
        ("thread.py", "threading.Thread", "threading.Thread is not in the lowered subset of threading"),
        ("event.py", "threading.Event", "threading.Event is not in the lowered subset of threading"),
        ("condition.py", "threading.Condition", "threading.Condition is not in the lowered subset of threading"),
        ("semaphore.py", "threading.Semaphore", "threading.Semaphore is not in the lowered subset of threading"),
    ]
    for name, expr, text in cases:
        source = f"import threading\ndef probe() -> int:\n    {expr}\n    return 1\n"
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
