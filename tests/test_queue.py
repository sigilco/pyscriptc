"""queue.Queue, LifoQueue and SimpleQueue on the runtime queues.

Issue #172, Stage C: the queue surface lowers onto a runtime record with a
mutex and two condvars, so a producer and a consumer can meet on one queue.
`queue.Empty` and `queue.Full` ride the reserved code space, next to the
coroutine exceptions, so user exception codes never shift.

Every oracle case runs the same source under CPython and the compiled binary
and compares stdout. Determinism comes from join(), never from scheduling.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case

_QUEUE_FIFO_SOURCE = """
import queue


def checklist() -> int:
    q: queue.Queue[int] = queue.Queue()
    if not q.empty():
        return 1
    q.put(1)
    q.put(2)
    q.put(3)
    if q.qsize() != 3:
        return 2
    if q.empty():
        return 3
    a: int = q.get()
    b: int = q.get()
    c: int = q.get()
    if a != 1:
        return 4
    if b != 2:
        return 5
    if c != 3:
        return 6
    if not q.empty():
        return 7
    return 0
"""

_QUEUE_LIFO_SOURCE = """
import queue


def checklist() -> int:
    q: queue.LifoQueue[int] = queue.LifoQueue()
    q.put(1)
    q.put(2)
    q.put(3)
    a: int = q.get()
    b: int = q.get()
    c: int = q.get()
    if a != 3:
        return 1
    if b != 2:
        return 2
    if c != 1:
        return 3
    return 0
"""

_QUEUE_SIMPLE_SOURCE = """
import queue


def checklist() -> int:
    q: queue.SimpleQueue[int] = queue.SimpleQueue()
    q.put(7)
    if q.qsize() != 1:
        return 1
    v: int = q.get()
    if v != 7:
        return 2
    if not q.empty():
        return 3
    return 0
"""

_QUEUE_BOUND_SOURCE = """
import queue


def checklist() -> int:
    q: queue.Queue[int] = queue.Queue(maxsize=2)
    q.put(1)
    q.put(2)
    if not q.full():
        return 1
    try:
        q.put_nowait(3)
    except queue.Full:
        pass
    else:
        return 2
    if q.qsize() != 2:
        return 3
    return 0
"""

_QUEUE_EMPTY_SOURCE = """
import queue


def checklist() -> int:
    q: queue.Queue[int] = queue.Queue()
    try:
        q.get_nowait()
    except queue.Empty:
        pass
    else:
        return 1
    return 0
"""

_QUEUE_FROM_IMPORT_SOURCE = """
from queue import Empty, Full, Queue


def checklist() -> int:
    q: Queue[int] = Queue()
    try:
        q.get_nowait()
    except Empty:
        pass
    else:
        return 1
    return 0
"""

_QUEUE_TASKS_SOURCE = """
import queue


def checklist() -> int:
    q: queue.Queue[int] = queue.Queue()
    q.put(1)
    q.put(2)
    q.get()
    q.task_done()
    q.get()
    q.task_done()
    q.join()
    return 0
"""

_QUEUE_THREADS_SOURCE = """
import queue
import threading


def checklist() -> int:
    q: queue.Queue[int] = queue.Queue()

    def producer(n: int) -> None:
        i: int = 0
        while i < n:
            q.put(i)
            i = i + 1

    t: threading.Thread = threading.Thread(target=producer, args=(1000,))
    t.start()
    total: int = 0
    got: int = 0
    while got < 1000:
        v: int = q.get()
        total = total + v
        got = got + 1
    t.join()
    if total != 499500:
        return 1
    return 0
"""


def _check(tmp_path: Path, name: str, source: str) -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "checklist")
    assert got.stdout == "0\n"


def test_queue_is_fifo(tmp_path: Path) -> None:
    _check(tmp_path, "fifo.py", _QUEUE_FIFO_SOURCE)


def test_lifo_queue_is_lifo(tmp_path: Path) -> None:
    _check(tmp_path, "lifo.py", _QUEUE_LIFO_SOURCE)


def test_simple_queue_round_trip(tmp_path: Path) -> None:
    _check(tmp_path, "simple.py", _QUEUE_SIMPLE_SOURCE)


def test_bounded_queue_raises_full(tmp_path: Path) -> None:
    _check(tmp_path, "bound.py", _QUEUE_BOUND_SOURCE)


def test_empty_queue_raises_empty(tmp_path: Path) -> None:
    _check(tmp_path, "empty.py", _QUEUE_EMPTY_SOURCE)


def test_queue_exceptions_import_by_name(tmp_path: Path) -> None:
    _check(tmp_path, "fromimp.py", _QUEUE_FROM_IMPORT_SOURCE)


def test_task_done_and_join(tmp_path: Path) -> None:
    _check(tmp_path, "tasks.py", _QUEUE_TASKS_SOURCE)


def test_queue_carries_values_between_threads(tmp_path: Path) -> None:
    _check(tmp_path, "threads.py", _QUEUE_THREADS_SOURCE)
