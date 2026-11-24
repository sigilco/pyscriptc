# The classmethod class id (issue #447, divergence 10): `Class.method(...)`
# and `instance.method(...)` deliver the receiving class, `cls.attr` reads
# the class's constants and runtime attributes through the compile-time
# resolution, `cls.method(...)` is a static call, and inheritance keeps the
# receiver class's own lookup.
import sys


class Counter:
    total: int = 3
    NAME = "counter"

    def __init__(self, n: int) -> None:
        self.n = n

    @classmethod
    def get(cls) -> int:
        return cls.total

    @classmethod
    def describe(cls) -> str:
        return f"{cls.NAME}:{cls.get()}"

    @classmethod
    def make(cls, n: int) -> Counter:
        return cls(n + 1)

    @staticmethod
    def twice(x: int) -> int:
        return x * 2


class Sub(Counter):
    extra: int = 1


class Plain:
    kind = "plain"

    @classmethod
    def kind_of(cls) -> str:
        return cls.kind


def probe() -> int:
    print(Counter.get(), Counter.describe())
    c: Counter = Counter(1)
    print(c.get(), c.describe())
    print(Counter.twice(4), c.twice(5))
    made: Counter = Counter.make(2)
    print(made.n)
    print(Sub.get(), Sub.describe())
    print(Plain().kind_of(), Plain.kind_of())
    return 0


if __name__ == "__main__":
    sys.exit(probe())
