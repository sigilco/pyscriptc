import math


def root(x: float) -> float:
    return math.sqrt(x * x + 1.0)


def total(xs: list[int]) -> int:
    return sum(xs)


def rows(n: int) -> list[int]:
    return [0] * n


def parse(s: str) -> int:
    return int(s)


def hexval() -> int:
    return int("ff", 16)


def fill(n: int) -> list[float]:
    out = [0.0] * n
    i = 0
    while i < n:
        out[i] = 1.5 * i
        i += 1
    return out
