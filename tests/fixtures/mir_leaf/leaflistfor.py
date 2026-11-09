def total(xs: list[int]) -> int:
    n = 0
    for x in xs:
        n = n + x
    return n


def double_all(xs: list[float]) -> list[float]:
    out = []
    for x in xs:
        out.append(x * 2.0)
    return out
