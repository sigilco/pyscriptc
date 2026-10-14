"""A loop that breaks out of an inner while."""


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        j: int = 0
        while j < 20:
            if j == 3:
                break
            total = total + 1
            j = j + 1
        i = i + 1
    return total
