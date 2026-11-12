# ruff: noqa: UP046, UP047 - the PEP 484 TypeVar spelling is the construct under test (PEP 695 fences PS1106)
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Generic, TypeVar

T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")
U = TypeVar("U", infer_variance=True)


def first(xs: Sequence[T]) -> T:
    return xs[0]


def opt_first(xs: list[T]) -> T | None:
    if not xs:
        return None
    return xs[0]


def or_else(x: T | None, d: T) -> T:
    if x is None:
        return d
    return x


def keys_of(m: Mapping[K, V]) -> list[K]:
    out: list[K] = []
    for k in m:
        out.append(k)
    return out


def get_or(m: dict[K, V], k: K, dflt: V) -> V:
    return m.get(k, dflt)


def apply(f: Callable[[T], U], x: T) -> U:
    return f(x)


def collect(xs: Iterable[T]) -> list[T]:
    out: list[T] = []
    for x in xs:
        out.append(x)
    return out


class Box(Generic[T]):
    def __init__(self, v: T | None) -> None:
        self.v: T | None = v

    def get(self, d: T) -> T:
        if self.v is None:
            return d
        return self.v


def sz(s: str) -> int:
    return len(s)


def maybe(flag: bool) -> int | None:
    if flag:
        return 4
    return None


def probe() -> str:
    a: list[int] = [3, 4]
    b: list[str] = ["x", "y"]
    e: list[int] = []
    d: dict[str, int] = {"a": 1}
    parts: list[str] = [str(first(a)), first(b), str(opt_first(e) is None)]
    s = opt_first(b)
    if s is not None:
        parts.append(s)
    parts.append(str(or_else(maybe(False), 1)) + str(or_else(maybe(True), 1)) + or_else(None, "n"))
    parts.append(",".join(keys_of(d)) + str(get_or(d, "z", 9)) + str(apply(sz, "hello")) + str(collect([1.5])[0]))
    bx: Box[str] = Box("q")
    parts.append(bx.get("z"))
    return " ".join(parts)


print(probe())
