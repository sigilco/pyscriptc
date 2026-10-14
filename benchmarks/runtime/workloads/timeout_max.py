"""The harness result for `threading.TIMEOUT_MAX`."""

import threading


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        value: float = threading.TIMEOUT_MAX
        if value != value:
            total = total + 2
        else:
            total = total + 1
        i = i + 1
    return total
