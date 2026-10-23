"""Task lifetime (issue #179, stage D1): `asyncio.create_task` and `await task`.

A task wraps a coroutine frame and outlives a single step, so a handle to a
running coroutine stays valid across a suspension. `create_task` schedules the
task instead of running it inline (CPython's semantics), `await task` parks
the awaiting coroutine on the task, and `task.result()` reads the value the
coroutine produced.

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

from pyscriptc.coro import AWAIT_TASK_MARKER, TASK_CLASS
from pyscriptc.ir import validate_module
from pyscriptc.lower import lower_source

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
ENV.pop("PYSC_CORO", None)

# `create_task` schedules the task; the body runs on the next loop iteration.
CREATE = """\
import asyncio


async def work() -> int:
    await asyncio.sleep(0.01)
    print("work done")
    return 42


async def main() -> int:
    t: asyncio.Task[int] = asyncio.create_task(work())
    print("after create")
    r: int = await t
    print("result", r)
    return r


print(asyncio.run(main()))
"""

# A task nobody awaits still runs to completion: `asyncio.run` waits for the
# outstanding tasks before it returns.
FIRE_AND_FORGET = """\
import asyncio


async def work() -> None:
    await asyncio.sleep(0.01)
    print("work done")


async def main() -> int:
    asyncio.create_task(work())
    await asyncio.sleep(0.05)
    print("main done")
    return 1


print(asyncio.run(main()))
"""

# The task starts on the next loop iteration, never inline.
START_ORDER = """\
import asyncio


async def work() -> None:
    print("work starts")


async def main() -> int:
    t: asyncio.Task[None] = asyncio.create_task(work())
    print("after create")
    await t
    print("after await")
    return 1


print(asyncio.run(main()))
"""

# Two tasks interleave the way CPython's do: both start, then both wake in
# deadline order.
INTERLEAVE = """\
import asyncio


async def work(n: int) -> int:
    print("start", n)
    await asyncio.sleep(0.02)
    print("wake", n)
    return n * 10


async def main() -> int:
    a: asyncio.Task[int] = asyncio.create_task(work(1))
    b: asyncio.Task[int] = asyncio.create_task(work(2))
    print("created")
    ra: int = await a
    rb: int = await b
    print("results", ra, rb)
    return ra + rb


print(asyncio.run(main()))
"""

# `done()` and `result()` read the task record.
RESULT = """\
import asyncio


async def work() -> int:
    await asyncio.sleep(0.01)
    return 42


async def main() -> int:
    t: asyncio.Task[int] = asyncio.create_task(work())
    print("done before", t.done())
    r: int = await t
    print("done after", t.done())
    print("result", t.result())
    return r


print(asyncio.run(main()))
"""

# A string result: the value is a two-word struct, so the read has to go
# through an out pointer.
STR_RESULT = """\
import asyncio


async def work() -> str:
    await asyncio.sleep(0.01)
    return "hello world"


async def main() -> str:
    t: asyncio.Task[str] = asyncio.create_task(work())
    r: str = await t
    print("awaited", r)
    s: str = t.result()
    print("result", s)
    return r


print(asyncio.run(main()))
"""

# A task whose coroutine awaits another coroutine.
NESTED = """\
import asyncio


async def inner() -> int:
    await asyncio.sleep(0.01)
    return 7


async def outer() -> int:
    v: int = await inner()
    return v + 1


async def main() -> int:
    t: asyncio.Task[int] = asyncio.create_task(outer())
    r: int = await t
    print("nested", r)
    return r


print(asyncio.run(main()))
"""

# A task created outside a coroutine, awaited inside one.
OUTSIDE = """\
import asyncio


async def work() -> int:
    await asyncio.sleep(0.01)
    return 3


async def main() -> int:
    r: int = await work()
    print("outside", r)
    return r


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
        ("create", CREATE),
        ("fire_and_forget", FIRE_AND_FORGET),
        ("start_order", START_ORDER),
        ("interleave", INTERLEAVE),
        ("result", RESULT),
        ("str_result", STR_RESULT),
        ("nested", NESTED),
        ("outside", OUTSIDE),
    ],
)
def test_task_matches_cpython(tmp_path: Path, name: str, source: str) -> None:
    got, expected = _run_both(tmp_path, name, source)
    assert expected
    assert got == expected


def test_task_ir_is_plain_classes() -> None:
    """The task handle is a runtime pointer: the IR carries no marker."""
    module = lower_source(CREATE, "create.py")
    assert module["fences"] == []
    validate_module(module)
    assert AWAIT_TASK_MARKER not in json.dumps(module)


def test_task_type_carries_the_result_type() -> None:
    """`asyncio.Task[int]` is the handle's type: the result type rides on it."""
    module = lower_source(CREATE, "create.py")
    task_types = [
        field["type"] for cls in module["classes"] for field in cls["fields"] if field["type"].get("name") == TASK_CLASS
    ]
    assert task_types, "the task handle is not typed"
    for typ in task_types:
        assert typ["elem"] == {"kind": "int"}


def test_await_of_a_task_is_not_a_child_poll() -> None:
    """`await task` parks on the task; it does not poll a child frame."""
    module = lower_source(CREATE, "create.py")
    text = json.dumps(module)
    assert "ps_task_await" in text


# --- a task that fails (issue #337, stage C) --------------------------------

FAILURE = """\
import asyncio


async def boom() -> int:
    await asyncio.sleep(0.01)
    print("boom")
    raise ValueError("late")


async def main() -> int:
    t: asyncio.Task[int] = asyncio.create_task(boom())
    try:
        r: int = await t
        print("never", r)
    except ValueError as e:
        print("caught from the await", str(e))
    u: asyncio.Task[int] = asyncio.create_task(boom())
    await asyncio.sleep(0.05)
    try:
        r2: int = u.result()
        print("never", r2)
    except ValueError as e:
        print("caught from the result", str(e))
    return 1


print(asyncio.run(main()))
"""


def test_a_failing_task_matches_cpython(tmp_path: Path) -> None:
    got, expected = _run_both(tmp_path, "task_failure", FAILURE)
    assert expected
    assert got == expected


def test_a_failing_task_re_raises_in_the_awaiter() -> None:
    """The await of a failed task re-raises the task's exception.

    The task runs on the loop's stack, so its exception has no handler
    below it: the record keeps it and the awaiting frame re-raises it.
    """
    module = lower_source(FAILURE, "task_failure.py")
    assert module["fences"] == []
    text = json.dumps(module)
    assert "ps_task_failure" in text
    validate_module(module)
