# Issue #413: unions, generic aliases and forward references as erased
# annotations. The probe runs on the pinned CPython oracle and on the
# compiled program: the stdout, stderr and exit code must match byte for
# byte.
#
# The erased union carries its first arm's layout (divergence 73), so the
# arms' values lower exactly like the hand-narrowed spelling; the alias and
# the quoted forward reference resolve through the same tables the unquoted
# spelling uses.
from typing import TypeAlias

Ids: TypeAlias = list[int]


def take(x: int | str) -> int:
    return 1


def pick(x: bytes | str) -> str:
    return "picked"


def first(xs: Ids) -> int:
    return xs[0]


def make() -> Leaf:
    return Leaf(3)


class Leaf:
    val: int

    def __init__(self, val: int) -> None:
        self.val = val


def read(node: Leaf) -> int:
    return node.val


def chain(n: Leaf | None) -> int:
    if n is None:
        return 0
    return n.val


def run() -> int:
    print(take(5), pick(b"x"))
    print(first([7, 8, 9]))
    leaf = make()
    print(read(leaf), chain(leaf), chain(None))
    aliased: Ids = [1, 2]
    print(aliased[0] + aliased[1])
    return 0


run()
