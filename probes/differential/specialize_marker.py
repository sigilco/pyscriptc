"""Differential probe: the `@specialize` marker of the shadow annotation module.

`pyscriptc._annotations.specialize` is the identity on plain CPython, so the
decorated definition is the same object the interpreter would see without the
decorator, and the program answers the same on both sides. Under the compiler
the marker asserts the per-call-site monomorphization the lowering already
performs for a `TypeVar`-generic function and for a `Generic[T]` class
(issue #1309), and the definition's own behavior is unchanged.

The program exercises the marker on a generic function and on a generic
class, with an unmarked sibling beside them: the marker is per definition,
so the sibling keeps its own copies and its own output.
"""

from typing import Generic, TypeVar

from pyscriptc._annotations import specialize

T = TypeVar("T")


@specialize
def first(xs: list[T]) -> T:
    return xs[0]


def plain(xs: list[int]) -> int:
    return xs[0]


@specialize
class Box(Generic[T]):
    def __init__(self, v: T) -> None:
        self.v: T = v

    def get(self) -> T:
        return self.v


class PlainBox:
    def __init__(self, v: int) -> None:
        self.v: int = v

    def get(self) -> int:
        return self.v


def probe() -> str:
    numbers: list[int] = [3, 4]
    words: list[str] = ["x", "y"]
    parts: list[str] = [str(first(numbers)), first(words), str(plain(numbers))]
    ints: Box[int] = Box(7)
    strs: Box[str] = Box("q")
    plain_box: PlainBox = PlainBox(9)
    parts.append(str(ints.get()))
    parts.append(strs.get())
    parts.append(str(plain_box.get()))
    return " ".join(parts)


print(probe())
