# SPDX-License-Identifier: Apache-2.0
"""A starred expression in a list display (issue #1246).

`[*a, b]` and `[b, *a]` build a new list with the starred elements
expanded in place, in source order. A starred list of lists splices one
level deep, the way CPython does. Both sides run this same program and
the lane byte-compares stdout.
"""

a: list[int] = [1, 2]
b: list[int] = [0, *a, 3]
print(b)

c: list[int] = [*a, 0]
print(c)

d: list[int] = []
print([*d, 1])

e: list[int] = [7]
f: list[int] = [*e, *a, *e]
print(f)

g: list[list[int]] = [[1], [2]]
h: list[list[int]] = [*g]
print(h)

i: list[int] = [*a, *a]
print(i)
