"""abs, min, max, sum, sorted, ord, chr, round, isinstance, getattr, hasattr."""


class Point:
    year: int

    def __init__(self, year: int) -> None:
        self.year = year


def bench(n: int) -> int:
    point: Point = Point(2020)
    total: int = 0
    i: int = 0
    while i < n:
        xs: list[int] = [i, 1, 2]
        ordered: list[int] = sorted(xs)
        letter: int = 65 + (i - (i // 26) * 26)
        total = total + abs(i - 3) + min(i, 3) + max(i, 3) + sum(xs) + ordered[0]
        total = total + ord(chr(letter)) + round(2.5)
        if isinstance(point, Point):
            total = total + getattr(point, "year")  # noqa: B009
        if hasattr(point, "year"):
            total = total + 1
        i = i + 1
    return total
