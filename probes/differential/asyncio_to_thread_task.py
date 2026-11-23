# SPDX-License-Identifier: Apache-2.0
"""`asyncio.to_thread` is a coroutine frame, so the task machinery takes it.

The call lowers to the same shape `async def` does: `create_task` wraps
the frame in a task, `gather` registers it, and `wait_for` runs it under
a deadline. The probe runs the same program on both sides and the lane
byte-compares stdout, stderr and the exit code.

The shapes here are the ones a caller writes: a task over `to_thread`, a
gather that mixes a thread-backed frame with a plain coroutine, a
wait_for that completes and one whose deadline fires first, and a task
cancelled before its first step. The thread itself still runs to
completion after a cancel (the D3 record semantics).
"""

import asyncio
import time


def work(n: int) -> int:
    return n * 2


def slow(n: int) -> int:
    time.sleep(0.05)
    return n + 5


async def child() -> int:
    await asyncio.sleep(0.01)
    return 7


async def main() -> None:
    # A task over `to_thread`: the worker's result comes back through the
    # record the frame parked on.
    task: asyncio.Task[int] = asyncio.create_task(asyncio.to_thread(work, 21))
    print("task", await task)

    # `gather` over a thread-backed frame and a plain coroutine: results
    # come back in argument order.
    results: list[int] = await asyncio.gather(asyncio.to_thread(work, 2), child())
    print("gather", results[0], results[1])

    # `wait_for` where the worker finishes first.
    print("wait_for", await asyncio.wait_for(asyncio.to_thread(work, 10), 5.0))

    # `wait_for` where the deadline fires first: the task is cancelled and
    # the worker still runs to completion.
    try:
        await asyncio.wait_for(asyncio.to_thread(slow, 1), 0.01)
        print("no timeout")
    except TimeoutError:
        print("timed out")

    # A cancel before the task's first step: the awaiter sees
    # CancelledError and the worker never started.
    pending: asyncio.Task[int] = asyncio.create_task(asyncio.to_thread(work, 1))
    pending.cancel()
    try:
        await pending
        print("no cancel")
    except asyncio.CancelledError:
        print("cancelled")


asyncio.run(main())
