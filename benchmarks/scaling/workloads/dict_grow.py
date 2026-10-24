"""Workload program for dict_grow scaling probe (#387)."""

from __future__ import annotations


def bench(n: int) -> int:
    d: dict[int, int] = {}
    i: int = 0
    while i < n:
        d[i] = i * 2
        i = i + 1
    total: int = 0
    for k in d.keys():
        total = total + k
    return total + len(d)


def dict_grow(n: int) -> int:
    return bench(n)
