"""operator.add, floordiv, and concat."""

import operator


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + operator.add(i, 1)
        total = total + operator.floordiv(i, 2)
        total = total + len(operator.concat("a", "b"))
        i = i + 1
    return total
