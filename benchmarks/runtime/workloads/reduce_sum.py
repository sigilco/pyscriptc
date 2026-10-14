"""functools.reduce of a direct (int, int) -> int function."""

import functools


def add(a: int, b: int) -> int:
    return a + b


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + functools.reduce(add, [1, 2, 3, 4], i)
        i = i + 1
    return total
