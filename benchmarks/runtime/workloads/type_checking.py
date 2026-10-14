"""A module if TYPE_CHECKING is omitted. The checksum is n times len('Ada')."""

from typing import TYPE_CHECKING

if not TYPE_CHECKING:
    word: str = "Ada"
else:
    import argparse  # noqa: F401


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + len(word)  # type: ignore[name-defined]
        i = i + 1
    return total
