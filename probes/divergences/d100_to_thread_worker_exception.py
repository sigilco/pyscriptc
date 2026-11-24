# SPDX-License-Identifier: Apache-2.0
# Divergence D100: an exception raised inside an asyncio.to_thread worker does
# not reach the awaiter. The worker record carries the result value and a done
# flag and no exception slot, so the worker's raise ends the program where the
# worker ran: CPython hands the exception to the awaiting task and the `except`
# clause runs, while the lowered program prints the exception on stderr and
# exits 1 without the awaiter ever seeing it.
import asyncio


def boom(n: int) -> int:
    raise ValueError("kaboom")


async def main() -> None:
    try:
        value: int = await asyncio.to_thread(boom, 1)
        print("no raise", value)
    except ValueError as exc:
        print("caught", exc)
    print("after")


asyncio.run(main())
