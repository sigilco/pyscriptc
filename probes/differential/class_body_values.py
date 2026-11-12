def make(n: int) -> list[int]:
    return [i * n for i in range(3)]


class Base:
    SHARED = [1, 2]
    KIND = "base"


class C(Base):
    a = make(2)
    b = [x + 1 for x in make(1)]
    c: list[str] = ["p", "q"]
    d = Base.SHARED
    e = len(Base.SHARED)

    def total(self) -> int:
        return len(self.a) + self.e + len(C.c)


def probe() -> int:
    print(C.a, C.b, C.c, C.d, C.e)
    print(C().a, C().d, C().total())
    print(Base.SHARED, Base.KIND)
    # The slot holds one object: a mutation through one read is visible
    # through the next, the identity CPython's class dictionary gives.
    seen = C.a
    seen.append(99)
    print(C.a)
    return 0
