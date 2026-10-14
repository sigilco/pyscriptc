"""A loop that continues past one value and keeps the rest."""


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        j: int = 0
        while j < 8:
            j = j + 1
            if j == 3:
                continue
            total = total + 1
        i = i + 1
    return total
