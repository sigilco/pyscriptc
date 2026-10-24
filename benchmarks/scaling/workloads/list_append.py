"""Workload program for list_append scaling probe (#387)."""

from __future__ import annotations


def bench(n: int) -> int:
    xs: list[int] = []
    i: int = 0
    while i < n:
        xs.append(i)
        i = i + 1
    total: int = 0
    for x in xs:
        total = total + x
    return total + len(xs)


def list_append(n: int) -> int:
    return bench(n)
