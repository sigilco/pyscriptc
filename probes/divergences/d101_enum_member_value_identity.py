# SPDX-License-Identifier: Apache-2.0
# Divergence D101: the enum member is the value itself, not a member object.
# CPython prints the member spelling (`Color.RED`) and answers the same
# member twice with one object, so the identity compare is True; the
# lowered program prints the value (`1`) and compares two values, so the
# identity compare is False. A flag combination is the integer sum in the
# lowered program and a member in CPython.
from enum import Enum, Flag, IntFlag


class Color(Enum):
    RED = 1
    GREEN = 2


class Perm(IntFlag):
    READ = 1
    WRITE = 2


class Bits(Flag):
    A = 1
    B = 2


print(Color.RED)
print(Color.RED is Color.RED)
print(Perm.READ | Perm.WRITE)
print(Bits.A | Bits.B)
