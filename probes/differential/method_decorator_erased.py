"""Differential probe: erased method/function decorators (divergence 54, issue #732).

A decorator outside the closed set that does not synthesize behavior
compiles as erased: the method (or module function) lowers undecorated,
so the decorator call never runs and the printed registry write and the
returned wrapper are missing where CPython replaces the attribute with
whatever the decorator returns. Every value the undecorated method
produces matches CPython; the `wrapped` lines are the divergence's
observable half, and the recorded row in
`docs/reference/divergence-ledger.json` captures both sides.
"""

from collections.abc import Callable


def deco(fn: Callable[[int], int]) -> Callable[[int], int]:
    print("wrapped")
    return fn


class Thing:
    @staticmethod
    def wrap(fn: Callable[[Thing], int]) -> Callable[[Thing], int]:
        print("wrapped method")
        return fn

    @wrap
    def total(self) -> int:
        return 3


@deco
def helper(n: int) -> int:
    return n + 4


def main() -> int:
    t: Thing = Thing()
    print(t.total())
    print(helper(4))
    return 0
