"""Differential probe: the `@island` marker of the shadow annotation module.

`pyscriptc._annotations.island` is the identity on plain CPython, so the
decorated definition is the same object the interpreter would see without
the decorator, and the program answers the same on both sides. Under the
compiler the marker routes that one definition to the island lane on
purpose (issue #1307), and the definition's own behavior is unchanged.

The program exercises the marker on a module function and on a class, with
an unmarked sibling beside them: the marker is per definition, so the
sibling keeps its own plane and its own output.
"""

from pyscriptc._annotations import island


@island
def marked(x: int) -> int:
    total: int = 0
    for i in range(x):
        total += i
    return total


def plain(x: int) -> int:
    total: int = 0
    for i in range(x):
        total += i
    return total


@island
class Marked:
    def value(self) -> int:
        return 7


class Plain:
    def value(self) -> int:
        return 7


print(marked(5))
print(plain(5))
print(Marked().value())
print(Plain().value())
