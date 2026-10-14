"""atexit.register of a no-argument function. Handlers run when the process exits."""

import atexit


def cleanup() -> None:
    return


def bench(n: int) -> int:
    i: int = 0
    while i < n:
        atexit.register(cleanup)
        i = i + 1
    return i
