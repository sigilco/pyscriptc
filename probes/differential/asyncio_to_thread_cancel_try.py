# SPDX-License-Identifier: Apache-2.0
"""`await task` inside a `try` survives the task's own cancel (issue #1425).

The task wraps a `to_thread` call, so the awaiting frame parks inside a
`ps.try` outline and the cancel path raises `CancelledError` at the
resume. The frame's copy-out then reads the await-result temp, whose type
is `none` when the awaited task produces no result, and that temp has to
stay addressable inside the outline.

The shapes: the issue program (an int result awaited inside `try`, the
cancel wins the race), a `str` result under the same `try`, the same
`try/except` shape around the awaited `to_thread` inside the task itself,
and a cancel before the task's first resume. The lane byte-compares
stdout, stderr and the exit code against the pinned CPython.
"""

import asyncio


def work(n: int) -> int:
    return n + 1


def swork(prefix: str) -> str:
    return prefix + "-done"


async def run() -> None:
    v: int = await asyncio.to_thread(work, 1)
    print("result", v)


async def run_str() -> None:
    v: str = await asyncio.to_thread(swork, "job")
    print("result", v)


async def inner() -> None:
    # `try/except` around the awaited `to_thread` inside the task: the
    # outline then lives in the task's own frame, not the awaiter's.
    try:
        v: int = await asyncio.to_thread(work, 5)
        print("inner", v)
    except asyncio.CancelledError:
        print("inner cancelled")
        raise


async def main() -> int:
    # The issue program: a task over `to_thread`, cancelled after a yield,
    # awaited inside a `try` that catches CancelledError.
    t: asyncio.Task[None] = asyncio.create_task(run())
    await asyncio.sleep(0.01)
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        print("cancelled int")

    # A str result under the same shape: the outline's env carries the
    # None-typed result temp either way.
    t2: asyncio.Task[None] = asyncio.create_task(run_str())
    await asyncio.sleep(0.01)
    t2.cancel()
    try:
        await t2
    except asyncio.CancelledError:
        print("cancelled str")

    # The try inside the task itself.
    t3: asyncio.Task[None] = asyncio.create_task(inner())
    await asyncio.sleep(0.01)
    t3.cancel()
    try:
        await t3
    except asyncio.CancelledError:
        print("cancelled inner")

    # A cancel before the task's first resume: the awaiter still sees
    # CancelledError, through the same outline.
    t4: asyncio.Task[None] = asyncio.create_task(run())
    t4.cancel()
    try:
        await t4
        print("no cancel")
    except asyncio.CancelledError:
        print("cancelled fresh")

    print("end")
    return 0


print(asyncio.run(main()))
