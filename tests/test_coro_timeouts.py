"""Timeouts and cancellation (issue #179, stage D2): `asyncio.wait_for` and the
task cancel surface.

`wait_for` runs the awaited coroutine as a task under a deadline: the deadline
fires first, the loop cancels the task and the awaiting frame raises
TimeoutError. `task.cancel()` asks a task to stop; the cancellation is
delivered at the task's next step, so `done()` and `cancelled()` read False
until then, and a task someone awaits raises CancelledError in the awaiting
frame.

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

from pyscriptc.ir import validate_module
from pyscriptc.lower import lower_source

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
ENV.pop("PYSC_CORO", None)

# The deadline wins over a slow coroutine.
TIMEOUT = """\
import asyncio


async def slow() -> int:
    await asyncio.sleep(2.0)
    return 5


async def main() -> int:
    try:
        v: int = await asyncio.wait_for(slow(), 0.05)
        print("got", v)
    except TimeoutError:
        print("timed out")
    return 1


print(asyncio.run(main()))
"""

# The coroutine wins over a generous deadline.
FAST = """\
import asyncio


async def quick() -> int:
    await asyncio.sleep(0.01)
    return 9


async def main() -> int:
    try:
        v: int = await asyncio.wait_for(quick(), 1.0)
        print("got", v)
    except TimeoutError:
        print("timed out")
    return 1


print(asyncio.run(main()))
"""

# An uncaught TimeoutError propagates out of asyncio.run.
UNCAUGHT = """\
import asyncio


async def slow() -> int:
    await asyncio.sleep(2.0)
    return 5


async def main() -> int:
    v: int = await asyncio.wait_for(slow(), 0.05)
    print("got", v)
    return 1


print(asyncio.run(main()))
"""

# A zero timeout times out before the coroutine runs; a finished coroutine
# meets any deadline.
ZERO = """\
import asyncio


async def quick() -> int:
    return 7


async def slow() -> int:
    await asyncio.sleep(5.0)
    return 9


async def main() -> int:
    a: int = await asyncio.wait_for(quick(), 1.0)
    print("a", a)
    try:
        b: int = await asyncio.wait_for(slow(), 0.0)
        print("b", b)
    except TimeoutError:
        print("b timed out")
    return 1


print(asyncio.run(main()))
"""

# A negative timeout is an immediate timeout, even on a task the caller
# already holds.
NEGATIVE = """\
import asyncio


async def slow() -> int:
    await asyncio.sleep(0.2)
    return 9


async def main() -> int:
    try:
        v: int = await asyncio.wait_for(slow(), -1.0)
        print("got", v)
    except TimeoutError:
        print("timed out")
    return 1


print(asyncio.run(main()))
"""

# wait_for on a task handle: the deadline applies to the task the caller
# holds, and the expiry cancels it.
TASK_DEADLINE = """\
import asyncio


async def slow() -> int:
    await asyncio.sleep(5.0)
    return 9


async def main() -> int:
    t: asyncio.Task[int] = asyncio.create_task(slow())
    await asyncio.sleep(0.01)
    try:
        v: int = await asyncio.wait_for(t, 0.05)
        print("got", v)
    except TimeoutError:
        print("timed out")
    print("cancelled:", t.cancelled())
    return 1


print(asyncio.run(main()))
"""

# cancel() returns True once and False on a second cancel or a done task.
CANCEL_RET = """\
import asyncio


async def slow() -> int:
    await asyncio.sleep(1.0)
    return 5


async def main() -> int:
    t: asyncio.Task[int] = asyncio.create_task(slow())
    await asyncio.sleep(0.01)
    print("cancel:", t.cancel())
    print("again:", t.cancel())
    print("cancelled:", t.cancelled())
    return 1


print(asyncio.run(main()))
"""

# The lifecycle: the cancel is pending until it is delivered, then the task
# reads done and cancelled.
CANCEL_LIFECYCLE = """\
import asyncio


async def slow() -> int:
    await asyncio.sleep(1.0)
    return 5


async def main() -> int:
    t: asyncio.Task[int] = asyncio.create_task(slow())
    await asyncio.sleep(0.01)
    print("cancel:", t.cancel())
    print("done:", t.done())
    print("cancelled:", t.cancelled())
    await asyncio.sleep(0.01)
    print("done after:", t.done())
    print("cancelled after:", t.cancelled())
    return 1


print(asyncio.run(main()))
"""

# A task cancelled before its first step never runs its body.
CANCEL_BEFORE_RUN = """\
import asyncio


async def never() -> int:
    await asyncio.sleep(5.0)
    print("never runs")
    return 1


async def main() -> int:
    t: asyncio.Task[int] = asyncio.create_task(never())
    c1: bool = t.cancel()
    d1: bool = t.done()
    print("cancel:", c1, "done:", d1)
    print("cancelled:", t.cancelled())
    return 1


print(asyncio.run(main()))
"""

# Awaiting a cancelled task raises CancelledError in the awaiting frame.
AWAIT_CANCELLED = """\
import asyncio


async def slow() -> int:
    await asyncio.sleep(1.0)
    return 5


async def main() -> int:
    t: asyncio.Task[int] = asyncio.create_task(slow())
    await asyncio.sleep(0.01)
    t.cancel()
    try:
        v: int = await t
        print("got", v)
    except asyncio.CancelledError:
        print("caught cancelled")
    return 1


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
        ("timeout", TIMEOUT),
        ("fast", FAST),
        ("zero", ZERO),
        ("negative", NEGATIVE),
        ("task_deadline", TASK_DEADLINE),
        ("cancel_ret", CANCEL_RET),
        ("cancel_lifecycle", CANCEL_LIFECYCLE),
        ("cancel_before_run", CANCEL_BEFORE_RUN),
        ("await_cancelled", AWAIT_CANCELLED),
    ],
)
def test_wait_for_matches_cpython(tmp_path: Path, name: str, source: str) -> None:
    got, expected = _run_both(tmp_path, name, source)
    assert expected
    assert got == expected


def test_uncaught_timeout_names_the_error(tmp_path: Path) -> None:
    """An uncaught TimeoutError exits 1 naming the error, like CPython."""
    path = tmp_path / "uncaught.py"
    path.write_text(UNCAUGHT, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
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
            str(tmp_path / "uncaught"),
        ],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    ran = subprocess.run([str(tmp_path / "uncaught")], capture_output=True, text=True)
    assert ran.returncode == expected.returncode == 1
    assert ran.stderr.strip().endswith("TimeoutError")


def test_wait_for_ir_is_a_marker() -> None:
    """`wait_for` lowers to the deadline suspension over a task, no fence.

    The coro pass consumes the marker (like the task-await marker), so the
    IR only shows the runtime calls the expansion emits.
    """
    module = lower_source(FAST, "fast.py")
    assert module["fences"] == []
    validate_module(module)
    text = json.dumps(module)
    assert "ps_task_wait_for" in text
    assert "ps_task_wait_outcome" in text
    assert "ps_task_new" in text


def test_cancelled_error_matches_through_the_reserved_code() -> None:
    """`except asyncio.CancelledError` matches the runtime's reserved code."""
    module = lower_source(AWAIT_CANCELLED, "await_cancelled.py")
    assert module["fences"] == []
    validate_module(module)
    text = json.dumps(module)
    assert "CancelledError" in text
    assert "ps_task_await" in text
