"""math.floor and math.sqrt. The hot path is the lowered math surface."""

import math


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + math.floor(math.sqrt(float(i + 1)))
        i = i + 1
    return total
