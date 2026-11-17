# SPDX-License-Identifier: Apache-2.0
# Issue #1279: `dataclasses.field(default_factory=...)`. The probe runs on
# the pinned CPython oracle and on the compiled program: the stdout,
# stderr and exit code must match byte for byte.
#
# SPDX-License-Identifier: Apache-2.0
#
# The byte-comparable surface: the three container factories give each
# instance its own list, dict and set (a mutation through one instance
# never shows in a fresh one), a callable factory (a module function and
# a lambda) is called per construction, a factory field sits after
# required fields and mixes with constant defaults, `__repr__` prints the
# factory-built containers, `__eq__` compares fieldwise (so two fresh
# instances with equal containers compare equal, and the identity check
# short-circuits the chain), and `!=` flips the answer.
from dataclasses import dataclass, field


def make_tags() -> list[str]:
    return ["a", "b"]


@dataclass
class Plain:
    tags: list[str] = field(default_factory=list)
    meta: dict[str, int] = field(default_factory=dict)
    flags: set[str] = field(default_factory=set)


@dataclass
class Mixed:
    name: str
    count: int = 0
    items: list[str] = field(default_factory=list)
    label: str = "d"


@dataclass
class Factored:
    tags: list[str] = field(default_factory=make_tags)
    lams: list[int] = field(default_factory=lambda: [1, 2])


@dataclass
class Point:
    x: int
    y: int


@dataclass
class Box:
    corner: Point = field(default_factory=lambda: Point(0, 0))
    tags: list[str] = field(default_factory=list)


one = Plain()
two = Plain()
print(one)
print(two)
one.tags.append("x")
one.meta["k"] = 1
one.flags.add("f")
print(one)
print(two)
print(Plain())
print(one.tags is two.tags)

m = Mixed("n")
print(m)
print(Mixed("n", 2, ["a"], "b"))

f1 = Factored()
f2 = Factored()
print(f1)
print(f1.tags is f2.tags)
f1.lams.append(3)
print(f2)

b1 = Box()
b2 = Box()
print(b1)
print(b1 == b2)
print(Box(Point(1, 2), ["t"]) == Box(Point(1, 2), ["t"]))
print(Box(Point(1, 2)) != Box(Point(1, 3)))
print(b1 == b1)
