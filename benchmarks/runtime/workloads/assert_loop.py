"""A loop that asserts a true condition and keeps counting."""


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        assert i >= 0
        total = total + 1
        i = i + 1
    return total
