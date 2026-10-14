"""The harness int for `asyncio.ALL_COMPLETED`."""

import asyncio


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        text: str = asyncio.ALL_COMPLETED
        total = total + len(text) * 1000 + ord(text[0])
        i = i + 1
    return total
