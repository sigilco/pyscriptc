"""collections.deque append, appendleft, pop, and popleft."""

import collections
from collections import deque


def bench(n: int) -> int:
    xs: deque[int] = deque([1])
    od: dict[str, int] = collections.OrderedDict()  # type: ignore[assignment]
    od["k"] = 1
    total: int = od["k"]
    i: int = 0
    while i < n:
        xs.append(i)
        xs.appendleft(i)
        xs.pop()
        xs.popleft()
        i = i + 1
    return total + len(xs)
