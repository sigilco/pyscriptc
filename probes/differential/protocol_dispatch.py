# Issue #445 (O3a): operator protocol dispatch. The resolved surfaces run on
# the pinned CPython oracle and on the compiled program: stdout, stderr and
# the exit code must match byte for byte.
#
# The fences (NotImplemented returns, mixed-class comparisons that the fixed
# order cannot decide, hash on an eq-without-hash class, class dict keys,
# runtime reflection) are compile-time refusals in the lowered lane and stay
# in the pytest oracle tests (tests/test_protocol_dispatch.py); this probe
# covers only what both sides run.
class Pt:
    x: int
    y: int

    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    def __eq__(self, other: Pt) -> bool:  # type: ignore[override]
        return self.x == other.x and self.y == other.y

    def __ne__(self, other: Pt) -> bool:  # type: ignore[override]
        return self.x != other.x or self.y != other.y

    def __lt__(self, other: Pt) -> bool:  # type: ignore[override]
        if self.x != other.x:
            return self.x < other.x
        return self.y < other.y

    def __le__(self, other: Pt) -> bool:  # type: ignore[override]
        if self.x != other.x:
            return self.x < other.x
        return self.y <= other.y

    def __gt__(self, other: Pt) -> bool:  # type: ignore[override]
        if self.x != other.x:
            return self.x > other.x
        return self.y > other.y

    def __ge__(self, other: Pt) -> bool:  # type: ignore[override]
        if self.x != other.x:
            return self.x > other.x
        return self.y >= other.y

    def __hash__(self) -> int:
        return self.x * 31 + self.y

    def __bool__(self) -> bool:
        return self.x != 0 or self.y != 0

    def __str__(self) -> str:
        return f"Pt({self.x}, {self.y})"

    def __repr__(self) -> str:
        return f"Pt({self.x!r}, {self.y!r})"

    def __format__(self, spec: str) -> str:
        return f"[{self.x}:{spec}]"


class Sub(Pt):
    pass


class Weak:
    # Defines __eq__ only: CPython makes the class unhashable, and the
    # subclass-priority rule picks Sub.__eq__ for a Sub == Pt pair.
    tag: int

    def __init__(self, tag: int) -> None:
        self.tag = tag

    def __eq__(self, other: Weak) -> bool:  # type: ignore[override]
        return self.tag == other.tag


class OnlyLen:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def __len__(self) -> int:
        return self.n


class NoProto:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def probe() -> int:
    a: Pt = Pt(1, 2)
    b: Pt = Pt(1, 2)
    c: Pt = Pt(1, 3)
    print(a == b, a == c, a != b, a != c)
    print(a < c, c < a, a <= b, a > b, a >= b, b >= a)
    sub: Sub = Sub(1, 2)
    print(sub == a, a == sub, sub != a, sub == b)
    weak: Weak = Weak(5)
    weak2: Weak = Weak(5)
    print(weak == weak2, weak != weak2)
    print(hash(a) == hash(b), hash(a) == hash(Pt(2, 2)))
    print(bool(a), bool(Pt(0, 0)), not a, "yes" if a else "no")
    short: OnlyLen = OnlyLen(0)
    print(bool(short), not short)
    while short:
        break
    print("loop ok")
    print(str(a), repr(a), f"{a}", f"{a!r}", f"{a:s}")
    print(f"{a:x}", f"{a}")
    plain: NoProto = NoProto(1)
    plain2: NoProto = NoProto(1)
    print(plain == plain2, plain != plain2, bool(plain))
    return 0


probe()
