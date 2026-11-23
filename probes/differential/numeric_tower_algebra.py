# SPDX-License-Identifier: Apache-2.0
"""The numeric tower and container algebra (issue #504).

True division (int/int, mixed int/float and float/float, and `/=` on a float
local), int and float modulo and floor division with CPython's sign rules
(the remainder takes the divisor's sign, and a zero result keeps the
divisor's sign), mixed int/float comparison including the case past 2**53,
sequence repetition for str, bytes, list and `*=`, set algebra with the
binary and in-place forms, and element-wise container equality (lists,
dicts, sets, tuples, nested containers, empty displays, int and float
element lists). Both sides run this same program and the lane
byte-compares stdout, stderr and the exit code.
"""


def members(s: set[int]) -> list[int]:
    """The members in ascending order (set iteration order is not CPython's)."""
    out: list[int] = []
    for v in range(12):
        if v in s:
            out.append(v)
    return out


# True division always answers a float, including for exact quotients.
a: int = 7
b: int = 2
print(a / b)
print(-7 / 2)
print(8 / 4)
print(0 / 5)
x: float = 7.5
print(x / 2)
print(7 / 2.5)
x /= 4
print(x)

# Int modulo and floor division: the remainder takes the divisor's sign.
print(7 % 3)
print(-7 % 3)
print(7 % -3)
print(-7 % -3)
print(-7 // 2)
print(7 // -2)
print(6 % 3)

# Float modulo and floor division follow float_divmod.
f1: float = 1.0
f2: float = 0.1
print(f1 // f2)
print(f1 % f2)
print(7.5 % 2)
print(-7.5 % 2)
print(-6.0 % 3.0)
z: float = -0.0
print(z % 3.0)
print(z // 3.0)
print(7.0 // 0.3)
print(5.0 % -2.5)

# Mixed int and float comparison: exact, including past 2**53.
print(1 < 2.0, 2 == 2.0, 1 <= 1.5, 2 >= 2.5, 3 != 3.0)
big: int = 9007199254740993
near: float = 9007199254740992.0
print(big == near, big != near, big < near, big > near, big >= near)
print(near == big, near < big, near <= big)
nan: float = float("nan")
print(nan == nan, nan != nan, 1 < nan, nan > 1, 1 == nan)
print(0 == -0.0, 0.0 == -0.0)
inf: float = float("inf")
print(1e308 * 10, -1e308 * 10, 3 < inf, -inf < 3)

# Repetition: str, bytes and list, on either side, and `*=`.
word: str = "ab"
print(word * 3)
print(3 * word)
print(word * 0)
print(word * -2)
ints: list[int] = [1, 2]
print(ints * 3)
print(2 * ints)
print(ints * 0)
ints *= 2
print(ints)
byt: bytes = b"xy"
print(byt * 2)
text: str = "ab"
text *= 2
print(text)

# Set algebra: the binary and the in-place forms.
s1: set[int] = {1, 2, 3}
s2: set[int] = {2, 3, 4}
print(members(s1 | s2))
print(members(s1 & s2))
print(members(s1 - s2))
print(members(s1 ^ s2))
s1 |= {9}
s1 &= {1, 2, 9}
s1 -= {2}
s1 ^= {1, 5}
print(members(s1))

# Container equality: element-wise and recursive.
l1: list[int] = [1, 2]
l2: list[int] = [1, 2]
l3: list[int] = [1, 3]
print(l1 == l2, l1 != l3, l1 == l3)
d1: dict[str, int] = {"a": 1}
d2: dict[str, int] = {"a": 1}
d3: dict[str, int] = {"a": 2}
print(d1 == d2, d1 == d3, d1 == {})
t1: tuple[int, str] = (1, "a")
t2: tuple[int, str] = (1, "a")
print(t1 == t2)
s3: set[int] = {1, 2}
s4: set[int] = {2, 1}
print(s3 == s4, s3 != s4)
nested1: list[list[int]] = [[1], [2, 3]]
nested2: list[list[int]] = [[1], [2, 3]]
print(nested1 == nested2)
dl1: dict[str, list[int]] = {"k": [1, 2]}
dl2: dict[str, list[int]] = {"k": [1, 2]}
print(dl1 == dl2)
empty: list[int] = []
print(empty == [], [] != l1, l1 == [])
fl: list[float] = [1.5, 2.0]
il: list[int] = [1, 2]
print(fl == il, il == [1, 2])
