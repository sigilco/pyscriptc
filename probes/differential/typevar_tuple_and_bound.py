# ruff: noqa: UP046, UP047 - the PEP 484 TypeVar spelling is the construct under test (PEP 695 fences PS1106)
"""The tuple-over-TypeVar and the bound-naming-a-class shapes (issue #412).

`tuple[T, int]` binds T from the argument tuple's first field, and the
tuple class is declared per call site; `tuple[T, ...]` is the homogeneous
shape (a list at the IR) and binds T from the list's element. A bound
naming a user class erases to that class, whether the class is declared
above or below the TypeVar.
"""

from typing import TypeVar


class Point:
    def __init__(self, n: int) -> None:
        self.n = n

    def twice(self) -> int:
        return self.n * 2


T = TypeVar("T")
B = TypeVar("B", bound=Point)


def head(x: tuple[T, int]) -> T:
    return x[0]


def wide(x: tuple[T, ...]) -> T:
    return x[0]


def rows(x: list[tuple[T, int]]) -> T:
    return x[0][0]


def twice(p: B) -> int:
    return p.twice()


def probe() -> str:
    parts: list[str] = [str(head((1, 2))), str(head(("a", 2)))]
    parts.append(str(wide([3, 4])))  # type: ignore[arg-type]
    parts.append(str(rows([(5, 6)])))
    parts.append(str(twice(Point(21))))
    return " ".join(parts)
