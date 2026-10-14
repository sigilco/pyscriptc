"""A loop whose condition is a mixed or of an int and a str."""


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        if i or "a":
            total = total + 1
        i = i + 1
    return total
