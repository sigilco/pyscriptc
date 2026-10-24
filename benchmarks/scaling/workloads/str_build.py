"""Workload program for str_build scaling probe (#387)."""

from __future__ import annotations


def bench(n: int) -> int:
    parts: list[str] = []
    i: int = 0
    while i < n:
        parts.append("a")
        i = i + 1
    s: str = "".join(parts)
    return len(s)


def str_build(n: int) -> int:
    return bench(n)
