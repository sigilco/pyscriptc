"""Float list membership via ==, including a NaN that is not a member."""

import math


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    nan: float = math.nan + 0.0
    while i < n:
        miss: int = 0
        if nan in [math.nan]:
            miss = 1
        hit: int = 0
        if 1.0 in [1.0]:
            hit = 1
        total = total + hit + miss
        i = i + 1
    return total
