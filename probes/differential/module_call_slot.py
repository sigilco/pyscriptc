"""Module-scope call results keep one object in a global slot (#502).

A call result assigned at module scope lowers once, at init, into the
same process slot the threading records and containers use. Every read
- at module scope or inside a function body - is that one value: the
call never re-runs, and identity holds across reads.
"""

import math
import os


def make(n: int) -> int:
    return n * 2


x = make(21)
y = math.sqrt(4)
z = os.getcwd()


def read_x() -> int:
    return x


def read_sum() -> float:
    return x + y


z2 = os.path.join(z, "probe")
two = make(x - 1)


def read_all() -> str:
    return f"{read_x()} {read_sum():.1f} {z2} {two}"


print(read_all())
print(z2.startswith(z))
print(two == 40)
