# SPDX-License-Identifier: Apache-2.0
"""The enum member surface (issue #451, phase O5a).

Every CPython enum member carries its value and its name, and an enum
class body can spell methods and properties that every member answers.
`StrEnum` joins the base set (its members are strs), and the flag bases
join as the integer subset (an `IntFlag` member is an int, so the bit
operators run as the integer ops).

The lowered enum answers the same surface: the value the body spelled and
the name the body spelled it under, the enum class's methods with the
member bound as `self`, and the flag arithmetic as integer arithmetic.
Both sides run this same program and the lane byte-compares stdout.

`print` of a member itself stays on the value (the divergence the enum
subset already carries), so the probe prints the member surface and the
arithmetic results, never a bare member.

The `auto()` counter reads the largest value the body has spelled, so an
explicit value moves it: `DEBUG = 10` then `auto()` gives 11, and an
explicit value after an `auto()` moves it again (`A = 5`, `auto()` gives
6, `C = 2`, `auto()` gives 7).
"""

from enum import Enum, Flag, IntEnum, IntFlag, StrEnum, auto


class Color(Enum):
    RED = 1
    GREEN = 2

    def label(self) -> str:
        return "lamp"

    @property
    def low(self) -> int:
        return 5


class State(IntEnum):
    OFF = auto()
    ON = auto()


class Name(StrEnum):
    FIRST = "first"
    SECOND = "second"

    def shout(self) -> str:
        return self.upper() + "!"


class Level(IntEnum):
    DEBUG = 10
    INFO = auto()
    WARN = auto()
    ERROR = auto()
    CRITICAL = auto()


class Mixed(IntEnum):
    A = 5
    B = auto()
    C = 2
    D = auto()


class Perm(IntFlag):
    READ = 1
    WRITE = 2
    EXEC = 4


class Bits(Flag):
    A = 1
    B = 2


print(Color.RED.value, Color.RED.name)
print(Color.GREEN.value, Color.GREEN.name)
print(State.OFF.value, State.OFF.name)
print(Level.DEBUG.value, Level.INFO.value, Level.WARN.value)
print(Level.ERROR.value, Level.CRITICAL.value)
print(Level.DEBUG.value + Level.INFO.value + Level.WARN.value + Level.ERROR.value + Level.CRITICAL.value)
print(Mixed.A.value, Mixed.B.value, Mixed.C.value, Mixed.D.value)
print(Name.FIRST.value, Name.FIRST.name)
print(Perm.READ.value, Perm.READ.name)
print(Bits.A.value, Bits.A.name)
print(Color.RED.label(), Color.GREEN.label())
print(Color.RED.low, Color.GREEN.low)
print(Name.FIRST.shout(), Name.SECOND.shout())
print(Perm.READ | Perm.WRITE)
print(Perm.EXEC & Perm.READ)
print(Perm.READ & Perm.READ)
print(Perm.READ | Perm.WRITE | Perm.EXEC)
