"""The lists corpus program: the island module the compiler routes to the engine.

A list crosses the seam element by element, so the element kind is part of
the ABI. The program covers an int list, a str list, an empty list and a
list built by the island itself (the return value is a fresh object, not the
argument).
"""


def echo_ints(items: list[int]) -> list[int]:
    return items


def echo_strs(items: list[str]) -> list[str]:
    return items


def empty() -> list[int]:
    return []


def built(n: int) -> list[int]:
    out: list[int] = []
    i: int = 0
    while i < n:
        out.append(i * i)
        i = i + 1
    return out


def total(items: list[int]) -> int:
    total: int = 0
    for item in items:
        total = total + item
    return total
