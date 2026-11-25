# SPDX-License-Identifier: Apache-2.0
"""Differential probe: the `__setattr__` write hook lowers to a direct call.

A class that defines a plain `def __setattr__(self, name: str, value: T)
-> None` method lowers every write on the class as a direct call of the
hook, with the attribute name as a compile-time string constant. The hook
runs before any descriptor lookup, the way CPython routes `c.a = v`
through `__setattr__` first.

The probe compares the hook dispatch: the hook runs with the attribute
name and the value, on the class and on a subclass through the MRO. The
hook body does not store to `self`: a self-write re-enters the hook in
CPython (a `RecursionError`) where the compiled program stores straight
to the field (divergence D108), and an observe-only hook leaves the
attribute unset in CPython (an `AttributeError` on the read) where the
compiled program reads the field slot. The dispatch itself is
byte-identical to the pinned CPython 3.14: stdout, stderr and the exit
code.
"""


class Counter:
    count: int

    def __init__(self, count: int) -> None:
        self.count = count

    def __setattr__(self, name: str, value: int) -> None:
        print("set", name, value)


class Base:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x

    def __setattr__(self, name: str, value: int) -> None:
        print("base", name, value)


class Child(Base):
    y: int

    def __init__(self, x: int, y: int) -> None:
        super().__init__(x)
        self.y = y


def main() -> int:
    c = Counter(1)
    c.count = 5
    b = Child(2, 3)
    b.y = 9
    return 0


main()
