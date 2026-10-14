"""The harness int for `warnings.defaultaction`."""

import warnings


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        text: str = warnings.defaultaction  # type: ignore[attr-defined]
        total = total + len(text) * 1000 + ord(text[0])
        i = i + 1
    return total
