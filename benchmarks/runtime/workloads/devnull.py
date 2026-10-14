"""Add `subprocess.DEVNULL` in a loop."""

import subprocess


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + subprocess.DEVNULL
        i = i + 1
    return total
