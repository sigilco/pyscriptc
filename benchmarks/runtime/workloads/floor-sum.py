"""Floor division, so the timed path cannot fall back to C truncation."""


def floor_sum(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + i // 3
        i = i + 1
    return total
