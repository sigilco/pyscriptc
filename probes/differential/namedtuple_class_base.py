# SPDX-License-Identifier: Apache-2.0
from typing import NamedTuple


class Point(NamedTuple):
    x: int
    y: str


class Tagged(NamedTuple):
    name: str
    count: int = 0


def run() -> int:
    p: Point = Point(1, "a")
    print(p.x)
    print(p.y)
    print(p[0])
    print(p[1])
    a, b = p
    print(a, b)
    print(p == Point(1, "a"))
    print(p == Point(2, "b"))
    q: Point = Point(x=3, y="c")
    print(q.x, q.y)
    r: Point = Point(4, y="d")
    print(r.x, r.y)
    t: Tagged = Tagged("tag")
    print(t.name, t.count)
    u: Tagged = Tagged("tag2", 7)
    print(u.name, u.count)
    return 0


print(run())
