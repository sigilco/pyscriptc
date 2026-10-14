"""A module re.compile of one str constant, read again on each iteration."""

import re

_NORMALIZE_PATTERN = re.compile(r"[-_.]+")


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    text: str = "ab--cd"
    while i < n:
        hits: list[str] = _NORMALIZE_PATTERN.findall(text)
        if len(hits) == 0:
            total = total - 1
        else:
            total = total + text.find(hits[0])
        i = i + 1
    return total
