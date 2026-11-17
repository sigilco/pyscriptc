# SPDX-License-Identifier: Apache-2.0
"""`asyncio.gather` collects the coroutine results into a list.

The lowerer builds a list literal (`restlower/_asyncio.py`) and the
checker reads the same list since issue #1278, so the annotation the
caller writes is `list[T]`. The probe runs the shapes a caller writes:
one gather, a gather nested in a loop, indexing, `len`, and `print` of
the whole list. Both sides run the same program and the lane
byte-compares stdout, stderr and the exit code.
"""

import asyncio


async def work(n: int) -> int:
    await asyncio.sleep(0.02)
    print("wake", n)
    return n * 10


async def root() -> int:
    # The annotation the issue fences on: a list, not a tuple.
    r: list[int] = await asyncio.gather(work(1), work(2), work(3))
    print("gather", r)
    print("len", len(r))
    print("first", r[0])
    total: int = 0
    # A gather inside a loop: each round drives its own batch.
    for i in range(2):
        batch: list[int] = await asyncio.gather(work(i + 4), work(i + 5))
        total += batch[0] + batch[1]
    print("total", total)
    return total


print("sum", asyncio.run(root()))
