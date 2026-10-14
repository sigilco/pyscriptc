"""contextlib.suppress and nullcontext. Even steps raise ValueError."""

from contextlib import nullcontext, suppress


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        with suppress(ValueError):
            if i // 2 * 2 == i:
                raise ValueError("x")
            total = total + 1
        with nullcontext():
            total = total + 1
        i = i + 1
    return total
