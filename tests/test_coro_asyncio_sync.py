"""The asyncio sync primitives (issue #180, stage D3): CPython-oracle runs.

`Lock`, `Event`, `Semaphore` and `Queue` are cooperative records in
`runtime/ps_async.c`: the `await` parks the *task* and the loop re-queues it
when the primitive can serve it. `to_thread` runs the callable on a real
pthread and the loop re-queues the awaiter when the worker marks the record
done (the spin is a recorded divergence).

Each sample runs under CPython and compiled; the compiled stdout must match
byte for byte.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc.coro import PRIM_MARKER, THREAD_MARKER
from pyscriptc.ir import validate_module
from pyscriptc.lower import lower_source

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
ENV.pop("PYSC_CORO", None)

# A lock serializes two tasks: the second acquires only after the first
# released, so `out 1` prints before `in 2`.
LOCK = """\
import asyncio


async def worker(lock: asyncio.Lock, n: int) -> None:
    await lock.acquire()
    print("in", n)
    await asyncio.sleep(0.01)
    lock.release()
    print("out", n)


async def main() -> int:
    lock: asyncio.Lock = asyncio.Lock()
    a: asyncio.Task[None] = asyncio.create_task(worker(lock, 1))
    b: asyncio.Task[None] = asyncio.create_task(worker(lock, 2))
    await a
    await b
    print("done")
    return 0


print(asyncio.run(main()))
"""

# `locked()` reads the flag: true while a holder is inside.
LOCKED = """\
import asyncio


async def main() -> int:
    lock: asyncio.Lock = asyncio.Lock()
    print("locked", lock.locked())
    await lock.acquire()
    print("locked", lock.locked())
    lock.release()
    print("locked", lock.locked())
    return 0


print(asyncio.run(main()))
"""

# An event: the waiter parks on `wait()` and `set()` wakes it; `is_set()`
# reads the flag and `clear()` drops it.
EVENT = """\
import asyncio


async def waiter(event: asyncio.Event) -> None:
    print("waiting")
    await event.wait()
    print("woke")


async def main() -> int:
    event: asyncio.Event = asyncio.Event()
    print("is_set", event.is_set())
    t: asyncio.Task[None] = asyncio.create_task(waiter(event))
    await asyncio.sleep(0.01)
    print("setting")
    event.set()
    await t
    print("is_set", event.is_set())
    event.clear()
    print("is_set", event.is_set())
    return 0


print(asyncio.run(main()))
"""

# A semaphore of one behaves like a lock; the count rides in `_value`.
SEMAPHORE = """\
import asyncio


async def worker(sem: asyncio.Semaphore, n: int) -> None:
    await sem.acquire()
    print("in", n)
    await asyncio.sleep(0.01)
    sem.release()
    print("out", n)


async def main() -> int:
    sem: asyncio.Semaphore = asyncio.Semaphore(1)
    print("value", sem._value)
    a: asyncio.Task[None] = asyncio.create_task(worker(sem, 1))
    b: asyncio.Task[None] = asyncio.create_task(worker(sem, 2))
    await a
    await b
    print("value", sem._value)
    return 0


print(asyncio.run(main()))
"""

# A queue: puts land in order, gets come out in order, and `qsize` reads
# the length.
QUEUE = """\
import asyncio


async def main() -> int:
    q: asyncio.Queue[int] = asyncio.Queue()
    await q.put(1)
    await q.put(2)
    await q.put(3)
    print("qsize", q.qsize())
    print("got", await q.get())
    print("got", await q.get())
    print("got", await q.get())
    print("empty", q.empty())
    return 0


print(asyncio.run(main()))
"""

# A queue get parks when the queue is empty: the producer is a second task,
# so the value crosses a real task boundary.
QUEUE_WAIT = """\
import asyncio


async def producer(q: asyncio.Queue[int]) -> None:
    await asyncio.sleep(0.01)
    await q.put(41)


async def main() -> int:
    q: asyncio.Queue[int] = asyncio.Queue()
    p: asyncio.Task[None] = asyncio.create_task(producer(q))
    v: int = await q.get()
    print("got", v)
    await p
    return 0


print(asyncio.run(main()))
"""

# `to_thread` runs the callable on a real thread and the await returns its
# value; an int crosses the boundary through the record's result slot.
TO_THREAD = """\
import asyncio


def work(n: int) -> int:
    return n * 2


async def main() -> int:
    v: int = await asyncio.to_thread(work, 21)
    print("got", v)
    return 0


print(asyncio.run(main()))
"""

# A str result: the two-word value crosses through the record's out pointer.
TO_THREAD_STR = """\
import asyncio


def work(prefix: str) -> str:
    return prefix + "-done"


async def main() -> int:
    v: str = await asyncio.to_thread(work, "job")
    print("got", v)
    return 0


print(asyncio.run(main()))
"""

# Two `to_thread` calls run while the loop spins: both finish, in either
# worker order, and each awaiter gets its own value.
TO_THREAD_TWO = """\
import asyncio


def work(n: int) -> int:
    return n + 1


async def main() -> int:
    a: asyncio.Task[int] = asyncio.create_task(asyncio.to_thread(work, 1))
    b: asyncio.Task[int] = asyncio.create_task(asyncio.to_thread(work, 2))
    ra: int = await a
    rb: int = await b
    print("results", ra, rb)
    return 0


print(asyncio.run(main()))
"""


def _run_both(tmp_path: Path, name: str, source: str) -> tuple[str, str]:
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, check=True).stdout
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--skip-check",
            "--emit",
            "bin",
            "-o",
            str(tmp_path / name),
            "--run",
        ],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    return built.stdout, expected


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("lock", LOCK),
        ("locked", LOCKED),
        ("event", EVENT),
        ("semaphore", SEMAPHORE),
        ("queue", QUEUE),
        ("queue_wait", QUEUE_WAIT),
        ("to_thread", TO_THREAD),
        ("to_thread_str", TO_THREAD_STR),
        ("to_thread_two", TO_THREAD_TWO),
    ],
)
def test_prim_matches_cpython(tmp_path: Path, name: str, source: str) -> None:
    got, expected = _run_both(tmp_path, name, source)
    assert expected
    assert got == expected


def test_prim_ir_is_plain_classes() -> None:
    """The primitives are runtime pointers: the IR carries no marker."""
    module = lower_source(LOCK, "lock.py")
    assert module["fences"] == []
    validate_module(module)
    dumped = json.dumps(module)
    assert PRIM_MARKER not in dumped
    assert THREAD_MARKER not in dumped


def test_to_thread_ir_is_plain_classes() -> None:
    module = lower_source(TO_THREAD, "to_thread.py")
    assert module["fences"] == []
    validate_module(module)
    assert THREAD_MARKER not in json.dumps(module)
