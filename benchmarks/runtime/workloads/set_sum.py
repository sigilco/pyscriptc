"""A set of int. The checksum is the sum of members plus len, which does not depend on order."""


def bench(n: int) -> int:
    xs: set[int] = set()
    i: int = 0
    while i < n:
        xs.add(i)
        xs.add(i)
        i = i + 1
    total: int = 0
    for item in xs:
        total = total + item
    return total + len(xs)
