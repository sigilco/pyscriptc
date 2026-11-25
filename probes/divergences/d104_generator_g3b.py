# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: UP035, UP043, UP045, UP006 - the typing.Iterator / typing.Optional spellings are the constructs under test
# Divergence D104: the G3b generator shapes (issue #442).
# A generator def nested inside a plain function lifts to the enclosing
# module's scope, a nested generator drives the send protocol, and a
# pass-through-decorated generator iterates through the wrapped frame
# twice. The probe runs the same program on both sides and the lane
# byte-compares stdout, stderr and the exit code.
from typing import Callable, Generator, Iterator, Optional


def outer(n: int) -> str:
    tag: str = "t:"

    def inner(start: int) -> Iterator[str]:
        i: int = start
        while i < n:
            yield tag + str(i)
            i += 1

    out: str = ""
    for v in inner(1):
        out += str(v) + ","
    return out


def echoer(base: int) -> str:
    def echo() -> Generator[Optional[int], Optional[int], None]:
        got: Optional[int] = None
        while True:
            got = yield got
            if got is None:
                return

    g: Generator[Optional[int], Optional[int], None] = echo()
    out: str = ""
    got: Optional[int] = g.send(None)
    out += str(got) + ";"
    got = g.send(base + 1)
    out += str(got) + ";"
    got = g.send(base * 10)
    out += str(got)
    return out


def identity(fn: Callable[[int], Iterator[int]]) -> Callable[[int], Iterator[int]]:
    return fn


@identity
def upto(n: int) -> Iterator[int]:
    i: int = 0
    while i < n:
        yield i
        i += 1


print(outer(4))
print(echoer(3))
s: str = ""
for v in upto(3):
    s += str(v)
for v in upto(2):
    s += str(v)
print(s)
