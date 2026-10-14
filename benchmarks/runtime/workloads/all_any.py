"""all(), any(), and a five-field tuple. The checksum is 7 per iteration."""


def bench(n: int) -> int:
    xs: list[int] = [1, 0, 2, 4, 0]
    total: int = 0
    i: int = 0
    while i < n:
        if all(xs):
            total = total + 1
        if any(xs):
            total = total + 2
        row: tuple[int, int, int, int, int] = (1, 2, 3, 4, 5)
        total = total + row[4]
        i = i + 1
    return total
