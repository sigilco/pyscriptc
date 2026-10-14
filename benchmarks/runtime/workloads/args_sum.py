"""*args of int, lowered as a list."""


def add(*xs: int) -> int:
    total: int = 0
    for item in xs:
        total = total + item
    return total


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + add(i, 1, 2)
        i = i + 1
    return total
