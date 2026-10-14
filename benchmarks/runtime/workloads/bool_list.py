"""bool() and a list() copy. The checksum does not depend on set order."""


def bench(n: int) -> int:
    base: list[int] = [1, 2, 3, 4]
    raw: list[int] = [1, 2, 2]
    seen: set[int] = set(raw)
    total: int = 0
    i: int = 0
    while i < n:
        copied: list[int] = list(base)
        if bool(copied):
            total = total + copied[0] + len(copied)
        if bool(seen):
            total = total + len(seen)
        i = i + 1
    return total
