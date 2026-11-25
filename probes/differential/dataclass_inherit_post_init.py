# SPDX-License-Identifier: Apache-2.0
"""Dataclass field flags, a dataclass base and `__post_init__` (issue #452).

The probe runs on the pinned CPython oracle and on the compiled program:
the stdout, stderr and exit code must match byte for byte.

The byte-comparable surface: the `repr=False` and `compare=False` flags
on `dataclasses.field` drop the field from the synthesized `__repr__`
and `__eq__`, a dataclass extends a dataclass base (the base's fields
lead the constructor and the child's follow), the child regenerates the
synthesized `__repr__` and `__eq__` over the whole field list, and
`__post_init__` runs after the fields are set (the child's own hook
wins over the base's, and `super().__post_init__()` calls the base's).
"""

from dataclasses import dataclass, field


@dataclass
class Flags:
    a: int
    hidden: str = field(default="h", repr=False)
    ignored: int = field(default=7, compare=False)
    both: str = field(default="b", repr=False, compare=False)


@dataclass
class Base:
    x: int
    label: str = "base"


@dataclass
class Child(Base):
    y: int = 3
    note: str = field(default="n", repr=False)


@dataclass
class Hooked:
    v: int

    def __post_init__(self) -> None:
        self.v = self.v * 2


@dataclass
class HookedChild(Hooked):
    w: int = 1

    def __post_init__(self) -> None:
        self.w = self.w + 10


@dataclass
class HookedInherit(Hooked):
    w: int = 1


@dataclass
class HookedSuper(Hooked):
    w: int = 1

    def __post_init__(self) -> None:
        super().__post_init__()
        self.w = self.w + 100


flags = Flags(1)
print(flags)
print(Flags(1, "z", 9, "q"))
print(flags == Flags(1, "z", 9, "q"))
print(flags == Flags(1))
print(flags == flags)

child = Child(1)
print(child)
print(Child(1) == Child(1))
print(Child(1) == Child(2))
print(Child(1, "l", 2, "other"))
print(Child(1, "l", 2) == Child(1, "l", 2))

print(Hooked(3))
print(HookedChild(3))
print(HookedInherit(3))
print(HookedSuper(3))
