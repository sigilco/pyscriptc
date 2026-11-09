def floats() -> list[float]:
    ys = []
    ys.append(1.5)
    ys.append(2.5)
    ys[0] = 3.5
    return ys


def head(xs: list[float]) -> float:
    return xs[0]


def sumsq(xs: list[float]) -> float:
    acc = 0.0
    i = 0
    n = len(xs)
    while i < n:
        acc = acc + xs[i] * xs[i]
        i = i + 1
    return acc
