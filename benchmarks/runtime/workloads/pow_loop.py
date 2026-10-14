"""A loop of small integer powers, each a loop of multiply."""


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + i ** 2
        i = i + 1
    return total
