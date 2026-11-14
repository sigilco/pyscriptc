import collections
import collections.abc
import collections.abc as cabc
from collections.abc import Collection, Mapping, MutableMapping, MutableSequence, MutableSet, Sequence
from collections.abc import Set as AbstractSet


def head(xs: collections.abc.Sequence[int]) -> int:
    return xs[0]


def tail(xs: cabc.Sequence[str]) -> str:
    return xs[-1]


def total(m: Mapping[str, int]) -> int:
    s: int = 0
    for k in m:
        s += m[k]
    return s


def bump(m: MutableMapping[str, int], key: str) -> None:
    m[key] = m.get(key, 0) + 1


def grow(xs: MutableSequence[int], v: int) -> None:
    xs.append(v)


def size(xs: Collection[int]) -> int:
    return len(xs)


def has(s: AbstractSet[int], v: int) -> bool:
    return v in s


def add(s: MutableSet[str], v: str) -> None:
    s.add(v)


def pick(xs: Sequence[float]) -> float:
    return xs[1]


nums: list[int] = [4, 5, 6]
words: list[str] = ["a", "b"]
d: dict[str, int] = {"x": 1, "y": 2}
print(head(nums), tail(words), total(d))
bump(d, "x")
bump(d, "z")
print(d)
grow(nums, 7)
print(nums, size(nums))
si: set[int] = {1, 2}
print(has(si, 2), has(si, 9))
ss: set[str] = set()
add(ss, "q")
print(len(ss))
fl: list[float] = [0.5, 1.5]
print(pick(fl))
dq: collections.deque[int] = collections.deque([1, 2])
dq.append(3)
print(len(dq), dq[0])
