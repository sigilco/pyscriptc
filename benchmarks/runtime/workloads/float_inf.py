"""float("inf") compared with math.inf, repeated."""

import math


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        hit: int = 0
        if float("inf") == math.inf:
            hit = 1
        neg: int = 0
        if float("-inf") < 0.0:
            neg = 1
        total = total + hit + neg
        i = i + 1
    return total
