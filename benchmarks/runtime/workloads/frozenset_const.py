"""frozenset of str constants is the set list. The checksum is len plus membership."""

REQUIRED = frozenset(["metadata_version", "name", "version"])


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        hit: int = 0
        if "name" in REQUIRED:
            hit = 1
        miss: int = 0
        if "missing" in REQUIRED:
            miss = 1
        total = total + len(REQUIRED) + hit + miss
        i = i + 1
    return total
