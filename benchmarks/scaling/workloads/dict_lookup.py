"""Workload program for dict_lookup scaling probe (#387)."""

from __future__ import annotations


def bench(n: int) -> int:
    d: dict[int, int] = {}
    i: int = 0
    while i < n:
        d[i] = i
        i = i + 1
    hits: int = 0
    j: int = 0
    while j < n:
        if j in d:
            hits = hits + 1
        j = j + 1
    return hits + len(d)


def dict_lookup(n: int) -> int:
    return bench(n)
