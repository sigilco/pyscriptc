"""Workload program for set_grow scaling probe (#387)."""

from __future__ import annotations


def bench(n: int) -> int:
    s: set[int] = set()
    i: int = 0
    while i < n:
        s.add(i)
        i = i + 1
    total: int = 0
    for item in s:
        total = total + item
    return total + len(s)


def set_grow(n: int) -> int:
    return bench(n)
