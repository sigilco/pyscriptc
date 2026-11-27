# SPDX-License-Identifier: Apache-2.0
"""A directly awaited `asyncio.to_thread` must run the awaiter once.

Issue #1296, the follow-up: a worker that finished between the park and the
poll left the waiter armed while the task ran on, so the next pump resumed
the finished task and its remaining statements ran twice. The window is
narrow, about one run in several thousand without the fix, so a single run
of this probe is a parity check and the race shows only across repeated runs.
"""

import asyncio


def work(n: int) -> int:
    return n * 2


async def main() -> int:
    v: int = await asyncio.to_thread(work, 21)
    print("got", v)
    return 0


print(asyncio.run(main()))
