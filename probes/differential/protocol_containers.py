# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: F821, B905 - the `__exit__` traceback parameter is spelled
# `TracebackType` (the CPython name, which the compiler erases onto the
# carrier slot without an import) and `zip(strict=)` is not in the lowered
# subset, so the probe keeps the CPython spellings.
# Issue #446 (O3b): container, iter and with protocol dispatch for user
# classes. The resolved surfaces run on the pinned CPython oracle and on
# the compiled program: stdout, stderr and the exit code must match byte
# for byte. Only what both sides run lives here; the compile-time fences
# stay in the pytest oracle tests.
class Box:
    items: list[int]

    def __init__(self) -> None:
        self.items = []

    def __getitem__(self, i: int) -> int:
        return self.items[i]

    def __setitem__(self, i: int, v: int) -> None:
        self.items[i] = v

    def __len__(self) -> int:
        return len(self.items)

    def __contains__(self, v: int) -> bool:
        i: int = 0
        while i < len(self.items):
            if self.items[i] == v:
                return True
            i = i + 1
        return False


class Countdown:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def __iter__(self) -> Countdown:
        return self

    def __next__(self) -> int:
        if self.n <= 0:
            raise StopIteration
        self.n = self.n - 1
        return self.n


class Range:
    lo: int
    hi: int

    def __init__(self, lo: int, hi: int) -> None:
        self.lo = lo
        self.hi = hi

    def __iter__(self) -> Countdown:
        return Countdown(self.hi - self.lo)


class Guard:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> str:
        print("enter", self.name)
        return self.name

    def __exit__(self, et: type[BaseException] | None, ev: BaseException | None, tb: TracebackType | None) -> bool:
        print("exit", self.name)
        return False


def probe() -> int:
    b: Box = Box()
    b.items = [10, 20, 30]
    print(b[0], b[2])
    b[1] = 99
    print(b[1])
    print(len(b))
    print(99 in b, 5 in b)

    s: int = 0
    for v in Countdown(3):
        s = s + v
    print(s)

    total: int = 0
    for v in Range(0, 4):
        total = total + v
    print(total)

    it: Countdown = Countdown(2)
    print(list(it))
    print(any(Countdown(2)), all(Countdown(2)))

    print(sum(Countdown(4)))

    i: int
    for i, v in enumerate(Countdown(3)):
        print(i, v)

    a: int
    c: int
    for a, c in zip(Countdown(3), Countdown(2)):
        print(a, c)

    for a, c in [(1, 2), (3, 4)]:
        print(a + c)

    with Guard("g") as name:
        print("body", name)
    return 0


if __name__ == "__main__":
    probe()
