"""class Box(object) is a bare class. The checksum is 2 * sum(range(n))."""


class Box(object):  # noqa: UP004 — the workload is the object base
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def twice(self) -> int:
        return self.n + self.n


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + Box(i).twice()
        i = i + 1
    return total
