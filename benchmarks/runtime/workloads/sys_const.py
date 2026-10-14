"""Baked sys.platform, sys.version, and sys.maxsize. No embedded interpreter."""

import sys


def bench(n: int) -> int:
    version: str = sys.version
    total: int = 0
    i: int = 0
    while i < n:
        if sys.platform == "linux":
            total = total + 1
        if sys.maxsize > 0:
            total = total + len(version)
        i = i + 1
    return total
