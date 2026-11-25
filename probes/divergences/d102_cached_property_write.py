# SPDX-License-Identifier: Apache-2.0
# Divergence D102: a write to a cached property name. CPython writes the
# value into the instance __dict__ (the descriptor is non-data, so the
# instance dictionary shadows it) and the name then answers the stored
# value without ever running the getter. The lowered program keeps the
# cached value in the struct slot and has no instance dictionary, so the
# write stops the compile with PS2001.
from functools import cached_property


class C:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x

    @cached_property
    def y(self) -> int:
        return self.x + 1


c = C(1)
print(c.y)
c.y = 5
print(c.y)
