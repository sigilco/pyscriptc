"""The harness int for `logging.BASIC_FORMAT`."""

import logging


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        text: str = logging.BASIC_FORMAT
        total = total + len(text) * 1000 + ord(text[0])
        i = i + 1
    return total
