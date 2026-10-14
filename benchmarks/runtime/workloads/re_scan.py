"""re.findall and re.sub on a short log line."""

import re


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    hay: str = "id=12 name=alice id=13 name=bob id=14 name=cara"
    while i < n:
        found: list[str] = re.findall(r"\d+", hay)
        swapped: str = re.sub(r"(a)(b)", r"\2\1", "ab")
        total = total + len(found[0]) + len(swapped)
        i = i + 1
    return total
