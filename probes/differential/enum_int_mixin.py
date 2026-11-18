# SPDX-License-Identifier: Apache-2.0
"""The integer enum surface (issue #1314).

CPython's `IntEnum` (and its `class C(int, Enum)` spelling) makes every
member an int: the member takes part in arithmetic, an enum annotation
takes an int, and an enum with no member yet is a base another enum may
extend, the way CPython allows it. The lowered enum does the same: the
member is an int constant, the annotation is the int type, and the
subclass fills the empty base. Both sides run this same program and the
lane byte-compares stdout.

`print` of a member itself stays on the value (the divergence the enum
subset already carries), so the probe prints arithmetic results only.
"""

from enum import Enum, IntEnum, auto


class Color(int, Enum):
    RED = 1
    GREEN = 2


class State(IntEnum):
    OFF = auto()
    ON = auto()


class Level(IntEnum):
    pass


class Named(Level):
    DEBUG = auto()
    INFO = auto()


def total(value: State) -> int:
    return value + 1


def pick(color: Color) -> int:
    if color == Color.GREEN:
        return 2
    return 1


print(Color.RED + Color.GREEN)
print(State.OFF + State.ON)
print(Named.DEBUG + Named.INFO)
print(total(State.ON))
print(pick(Color.RED), pick(Color.GREEN))
