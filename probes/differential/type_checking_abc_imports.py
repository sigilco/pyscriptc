from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from collections.abc import Set as AbstractSet


def app(f: Callable[[int], int], v: int) -> int:
    return f(v)


def inc(v: int) -> int:
    return v + 1


def total(m: Mapping[str, int]) -> int:
    s: int = 0
    for k in m:
        s += m[k]
    return s


def has(s: AbstractSet[str], v: str) -> bool:
    return v in s


d: dict[str, int] = {"a": 1, "b": 5}
names: set[str] = {"p", "q"}
print(app(inc, 2), total(d), has(names, "p"), has(names, "z"))
