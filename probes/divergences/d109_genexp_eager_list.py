# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: UP035 - the typing.Iterable spelling is the construct under test
# Divergence D109: generator expressions lower as an eager list (issue #449).
# CPython builds a lazily-evaluated generator: the element expression does not
# run at creation, the outermost iterable is captured by reference, and the
# generator is single-use. pyscriptc materializes the list at the point of
# evaluation, so the element side effects run at creation, later mutation of
# the iterable does not flow through, and a second drain sees the same list.
# The probe pins all three seams on both sides.
from typing import Iterable


def f(x: int) -> int:
    print("call", x)
    return x * 2


g = (f(i) for i in range(3))
print("created")
for v in g:
    print("v", v)

xs: list[int] = [1, 2, 3]
h = (x * 10 for x in xs)
xs.append(4)
print(list(h))

xs2: list[int] = [1, 2, 3]
k: Iterable[int] = (x * 10 for x in xs2)
a: int = 0
for v in k:
    a += v
b: int = 0
for v in k:
    b += v
print(a * 1000 + b)
