# SPDX-License-Identifier: Apache-2.0
"""Constant field defaults and `field(default=...)` (issue #444).

The class body's `x: T = <constant>` names the class attribute in
CPython, and an instance read falls back to it until the instance
writes its own. A written `__init__` that assigns `self.x` therefore
sees the constant first (`self.n = self.n + 1` reads 5) and shadows it
afterwards. The lowered class seeds the field slot with the constant,
the same observable order.

`dataclasses.field(default=<constant>)` is the spelled-out form of the
same default; `metadata=` names the field in the dataclass metadata and never
reaches the class's own value semantics, so it is accepted and dropped. Both sides run this same program and the lane
byte-compares stdout.
"""

from dataclasses import dataclass, field


class Counter:
    n: int = 5
    tag: str = "base"

    def bump(self) -> int:
        self.n = self.n + 1
        return self.n

    def tag_it(self) -> str:
        self.tag = self.tag + "!"
        return self.tag


class Ordered:
    late: int
    early: int = 3

    def __init__(self) -> None:
        self.late = self.early * 2


class Base:
    n: int = 1


class Sub(Base):
    def __init__(self) -> None:
        self.n = 5


@dataclass
class Spelled:
    a: int = field(default=7)
    b: str = field(default="hi")
    plain: float = 1.5


@dataclass
class Metadata:
    a: int = field(default=2, metadata={"fmt": "hex"})
    tags: list[str] = field(default_factory=list)


print(Counter().bump(), Counter().tag_it(), Counter.n, Counter.tag)
print(Ordered().late, Ordered.early)
print(Sub().n, Base.n)
print(Spelled().a, Spelled().b, Spelled().plain, Spelled(3).a, Spelled(3, "z").b)
print(Metadata().a, len(Metadata().tags), len(Metadata(tags=["x"]).tags))
