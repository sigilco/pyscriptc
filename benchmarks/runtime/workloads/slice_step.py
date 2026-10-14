"""A loop that takes every other character of a short string."""


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    text: str = "abcdefghij"
    while i < n:
        total = total + len(text[::2])
        i = i + 1
    return total
