"""Add the `re.DOTALL` int flag in a loop."""

import re


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + re.DOTALL
        i = i + 1
    return total
