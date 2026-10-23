"""Stage C (issue #337): `await` inside `try`, `except`, `finally` and `with`.

The generator side of this shape landed with #164 (#345): the guard transform
re-enters a `try`, the `finally` is guarded by the suspension, and an `except`
body that suspends keeps the exception it caught in the frame. This file pins
the coroutine side: every sample runs under CPython and compiled, and the
compiled stdout must match byte for byte.

The shapes here are the ones the plan's exit criterion names: a coroutine that
awaits inside a `try` and a `finally`, the exception path through the `try`,
and the `with` that desugars into one. The samples also cover the shapes the
guard transform has to get right: a suspension in the `except` body, one in
the `finally` body, a `try`/`else`, a nested `try`, and a suspension inside a
loop that sits in the `try` body.

Each sample is small on purpose: the receipt is the oracle match, not the
number of lines.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc.coro import AWAIT_MARKER
from pyscriptc.ir import validate_module
from pyscriptc.lower import lower_source

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
ENV.pop("PYSC_CORO", None)


def _run_both(tmp_path: Path, name: str, source: str) -> tuple[str, str]:
    """Run the sample under CPython and compiled; return both stdouts."""
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


TRY_FINALLY = """\
import asyncio


async def child() -> int:
    await asyncio.sleep(0.01)
    return 5


async def guarded() -> int:
    v: int = 0
    try:
        v = await child()
    finally:
        print("fin")
    return v


print(asyncio.run(guarded()))
"""


TRY_CATCHES = """\
import asyncio


async def boom() -> int:
    await asyncio.sleep(0.01)
    raise ValueError("late")


async def caught() -> int:
    try:
        await boom()
    except ValueError:
        print("caught")
    return 1


async def caught_in_finally() -> int:
    try:
        try:
            await boom()
        finally:
            print("inner fin")
    except ValueError:
        print("caught outer")
    return 1


print(asyncio.run(caught()))
print(asyncio.run(caught_in_finally()))
"""


SUSPEND_IN_HANDLER = """\
import asyncio


async def child() -> int:
    await asyncio.sleep(0.01)
    return 5


async def in_handler() -> int:
    try:
        raise ValueError("boom")
    except ValueError:
        print("caught")
        v: int = await child()
        print("awaited", v)
    return 1


print(asyncio.run(in_handler()))
"""


SUSPEND_IN_FINALLY = """\
import asyncio


async def child() -> int:
    await asyncio.sleep(0.01)
    return 5


async def in_finally() -> int:
    try:
        print("body")
    finally:
        v: int = await child()
        print("awaited", v)
    return 1


print(asyncio.run(in_finally()))
"""


TRY_ELSE = """\
import asyncio


async def child() -> int:
    await asyncio.sleep(0.01)
    return 5


async def main() -> int:
    try:
        v: int = await child()
    except ValueError:
        print("caught")
    else:
        print("else", v)
    finally:
        print("fin")
    return 1


print(asyncio.run(main()))
"""


NESTED_TRY = """\
import asyncio


async def child() -> int:
    await asyncio.sleep(0.01)
    return 5


async def main() -> int:
    try:
        try:
            v: int = await child()
            print("inner", v)
        finally:
            print("inner fin")
    except ValueError:
        print("outer caught")
    return 1


print(asyncio.run(main()))
"""


SUSPEND_IN_LOOP = """\
import asyncio


async def child() -> int:
    await asyncio.sleep(0.01)
    return 5


async def main() -> int:
    i: int = 0
    try:
        while i < 3:
            v: int = await child()
            print("loop", i, v)
            i = i + 1
    finally:
        print("fin")
    return 1


print(asyncio.run(main()))
"""


RAISE_AFTER_SUSPEND = """\
import asyncio


async def child() -> int:
    await asyncio.sleep(0.01)
    return 5


async def main() -> int:
    try:
        v: int = await child()
        print("after await", v)
        raise ValueError("after await")
    except ValueError:
        print("caught")
    finally:
        print("fin")
    return 1


print(asyncio.run(main()))
"""


BARE_RAISE_AFTER_SUSPEND = """\
import asyncio


async def child() -> int:
    await asyncio.sleep(0.01)
    return 5


async def main() -> int:
    try:
        try:
            raise ValueError("boom")
        except ValueError:
            await child()
            print("after await")
            raise
    except ValueError as e:
        print("outer caught", str(e))
    return 1


print(asyncio.run(main()))
"""


WITH_BODY = """\
import asyncio
import contextlib


async def child() -> int:
    await asyncio.sleep(0.01)
    return 5


async def in_with() -> int:
    with contextlib.nullcontext():
        v: int = await child()
        print("inside", v)
    return v


print(asyncio.run(in_with()))
"""


WITH_CATCHES_CHILD = """\
import asyncio
import contextlib


async def boom() -> int:
    await asyncio.sleep(0.01)
    raise ValueError("late")


async def main() -> int:
    with contextlib.suppress(ValueError):
        v: int = await boom()
        print("inside", v)
    print("after suppress")
    return 1


print(asyncio.run(main()))
"""


WITH_AND_TRY = """\
import asyncio
import contextlib


async def child() -> int:
    await asyncio.sleep(0.01)
    return 5


async def main() -> int:
    try:
        with contextlib.nullcontext():
            v: int = await child()
            print("inside", v)
    finally:
        print("fin")
    return v


print(asyncio.run(main()))
"""


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("try_finally", TRY_FINALLY),
        ("try_catches", TRY_CATCHES),
        ("suspend_in_handler", SUSPEND_IN_HANDLER),
        ("suspend_in_finally", SUSPEND_IN_FINALLY),
        ("try_else", TRY_ELSE),
        ("nested_try", NESTED_TRY),
        ("suspend_in_loop", SUSPEND_IN_LOOP),
        ("raise_after_suspend", RAISE_AFTER_SUSPEND),
        ("bare_raise_after_suspend", BARE_RAISE_AFTER_SUSPEND),
        ("with_body", WITH_BODY),
        ("with_catches_child", WITH_CATCHES_CHILD),
        ("with_and_try", WITH_AND_TRY),
    ],
)
def test_stage_c_matches_cpython(tmp_path: Path, name: str, source: str) -> None:
    got, expected = _run_both(tmp_path, name, source)
    assert expected
    assert got == expected


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("try_finally", TRY_FINALLY),
        ("try_catches", TRY_CATCHES),
        ("suspend_in_handler", SUSPEND_IN_HANDLER),
        ("suspend_in_finally", SUSPEND_IN_FINALLY),
        ("try_else", TRY_ELSE),
        ("nested_try", NESTED_TRY),
        ("suspend_in_loop", SUSPEND_IN_LOOP),
        ("raise_after_suspend", RAISE_AFTER_SUSPEND),
        ("bare_raise_after_suspend", BARE_RAISE_AFTER_SUSPEND),
        ("with_body", WITH_BODY),
        ("with_catches_child", WITH_CATCHES_CHILD),
        ("with_and_try", WITH_AND_TRY),
    ],
)
def test_stage_c_keeps_the_await_marker_out_of_the_ir(name: str, source: str) -> None:
    """The await lowers into the frame's own machinery, not a marker call."""
    module = lower_source(source, f"{name}.py")
    assert module["fences"] == []
    validate_module(module)
    assert AWAIT_MARKER not in json.dumps(module)


def test_a_bare_raise_after_a_suspension_re_raises() -> None:
    """The handler keeps the exception it caught across the suspension.

    A bare `raise` after the await in the handler re-raises the exception the
    handler caught, not something else: the frame holds it in `.ps_exc` and
    the resume puts it back.
    """
    module = lower_source(BARE_RAISE_AFTER_SUSPEND, "bare_raise.py")
    assert module["fences"] == []
    validate_module(module)
    blob = json.dumps(module)
    assert "ps_exc_restore" in blob
