"""A loop that folds a short int list with functools.reduce."""

import functools
import operator


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + functools.reduce(operator.add, [1, 2, 3])
        i = i + 1
    return total
