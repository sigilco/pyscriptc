"""A loop that calls a module staticmethod and keeps counting."""


@staticmethod  # type: ignore[misc]
def tick() -> int:
    return 1


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + tick()
        i = i + 1
    return total
