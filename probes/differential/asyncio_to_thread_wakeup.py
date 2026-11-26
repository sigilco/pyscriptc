# SPDX-License-Identifier: Apache-2.0
"""`asyncio.to_thread` must wake every parked task when its worker finishes.

Issue #1296: a worker can mark the record done during the loop's drain,
after that iteration's pump ran. The exit check then saw no pending
worker and returned while the parked task was never woken, so the program
printed only the final exit code. The probe runs the shapes that parked
two tasks on live records and byte-compares stdout, stderr and the exit
code against the pinned CPython: two concurrent `to_thread` calls (the
flake's reproducer) and a cancel that lands before the task's first
step.
"""

import asyncio


def work(n: int) -> int:
    return n + 1


async def run(n: int) -> int:
    v: int = await asyncio.to_thread(work, n)
    return v


async def main() -> int:
    # Two `to_thread` calls park two tasks on two live records; the pump
    # wakes both. The completion order is racy, so the results print
    # sorted.
    a: asyncio.Task[int] = asyncio.create_task(run(1))
    b: asyncio.Task[int] = asyncio.create_task(run(2))
    ra: int = await a
    rb: int = await b
    results: list[int] = [ra, rb]
    results.sort()
    print("results", results[0], results[1])

    # A cancel before the task's first step: the awaiter sees
    # CancelledError and the worker never started.
    t: asyncio.Task[int] = asyncio.create_task(asyncio.to_thread(work, 1))
    t.cancel()
    try:
        await t
        print("no cancel")
    except asyncio.CancelledError:
        print("cancelled")
    return 0


print(asyncio.run(main()))
