# SPDX-License-Identifier: Apache-2.0
"""`print` of a dataclass instance (issue #1246).

CPython's `@dataclass` synthesizes `__repr__` from the field list, so
printing an instance shows `P(x=1)`. The lowered class does the same:
the scalars ride `%r`, a list or dict field shows its own repr, and a
nested dataclass field shows the nested class's repr. A class that
writes its own `__repr__` or `__str__` keeps it. Both sides run this
same program and the lane byte-compares stdout.
"""

from dataclasses import dataclass


@dataclass
class Point:
    x: int
    y: int


@dataclass
class Shape:
    name: str
    size: float
    tags: list[str]
    counts: dict[str, int]


@dataclass
class Box:
    corner: Point
    depth: int


@dataclass
class Empty:
    pass


@dataclass
class Named:
    x: int

    def __repr__(self) -> str:
        return "Named!"


@dataclass
class Shouted:
    x: int

    def __str__(self) -> str:
        return "Shouted"


print(Point(1, 2))
print(Shape("box", 1.5, ["a", "b"], {"k": 1}))
print(Box(Point(3, 4), 5))
print(Empty())
print(Named(1))
print(Shouted(1))
