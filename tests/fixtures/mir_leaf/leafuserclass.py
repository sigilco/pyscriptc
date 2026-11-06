class Pair:
    a: int
    b: int

    def __init__(self, x: int, y: int) -> None:
        self.a = x
        self.b = y


def make_pair(n: int) -> Pair:
    return Pair(n, n + 1)


def sum_pair(p: Pair) -> int:
    return p.a + p.b


def build(depth: int) -> Pair:
    if depth == 0:
        return Pair(0, 0)
    inner = build(depth - 1)
    return Pair(inner.a + 1, inner.b + depth)


def main() -> None:
    p = build(3)
    print(p.a, sum_pair(make_pair(4)))
