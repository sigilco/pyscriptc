def build() -> list[int]:
    xs = [1, 2, 3]
    xs.append(4)
    xs[0] = 10
    return xs


def first(xs: list[int]) -> int:
    return xs[0]


def total(xs: list[int]) -> int:
    acc = 0
    i = 0
    n = len(xs)
    while i < n:
        acc = acc + xs[i]
        i = i + 1
    return acc


def pair(xs: list[int]) -> list[int]:
    ys = [xs[0], xs[1]]
    ys[1] = xs[0]
    return ys
