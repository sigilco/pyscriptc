"""Filtered list comprehensions, one if around the list push."""


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        xs: list[int] = [k for k in range(8) if k > 1]
        total = total + xs[0]
        i = i + 1
    return total
