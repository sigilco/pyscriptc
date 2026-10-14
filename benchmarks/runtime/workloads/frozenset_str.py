"""frozenset of a str constant is the set of its characters. The checksum is len plus membership."""

CHARS = frozenset(".0123456789")


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        hit: int = 0
        if "." in CHARS:
            hit = 1
        miss: int = 0
        if "a" in CHARS:
            miss = 1
        total = total + len(CHARS) + hit + miss
        i = i + 1
    return total
