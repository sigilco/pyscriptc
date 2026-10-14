"""Chained comparisons of ints, joined as compares and and."""


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        hit: int = 0
        if 0 < i < n:
            hit = 1
        total = total + hit
        i = i + 1
    return total
