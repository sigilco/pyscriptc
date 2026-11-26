# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: UP035, UP043 - the typing.Generator spelling is the construct under test (the frame's throw and close methods exist only on a generator frame)

from typing import Generator


def gen() -> Generator[int, None, None]:
    yield 1
    yield 2


g = gen()
print(next(g))
try:
    g.throw(ValueError("x"))
except ValueError as e:
    print("caught", repr(e))
