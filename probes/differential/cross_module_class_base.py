# SPDX-License-Identifier: Apache-2.0
"""Differential probe: the cross-module class base (issue #732).

A class that extends a class another module defines lowers natively: the
subclass inherits the base's fields and method surface, an override
resolves to the subclass body, and an inherited method reaches the
defining module's body. The probe mirrors the module split in one file
(the lane compiles single programs): the same definer/overrider shape the
cross-module import carries through the export table's MRO rows.
"""


class Base:
    def __init__(self, x: int) -> None:
        self.x = x

    def get_x(self) -> int:
        return self.x

    def tag(self) -> str:
        return "base:" + str(self.x)


class Child(Base):
    def __init__(self, x: int) -> None:
        super().__init__(x)

    def tag(self) -> str:
        return "child:" + str(self.get_x())


class Grand(Child):
    def __init__(self, x: int) -> None:
        super().__init__(x)


b: Base = Base(1)
print(b.tag(), b.get_x())
c: Child = Child(2)
print(c.tag(), c.get_x())
g: Grand = Grand(3)
# `tag` is Child's override; `get_x` is inherited from Base.
print(g.tag(), g.get_x())
