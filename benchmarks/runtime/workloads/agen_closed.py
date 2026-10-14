"""The harness int for `inspect.AGEN_CLOSED`."""

import inspect


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        text: str = inspect.AGEN_CLOSED
        total = total + len(text) * 1000 + ord(text[0])
        i = i + 1
    return total
