"""itertools.repeat and itertools.chain as for-iterators."""

import itertools


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        for rep in itertools.repeat(2, 3):
            total = total + rep
        for piece in itertools.chain([i], [1]):
            total = total + piece
        i = i + 1
    return total
