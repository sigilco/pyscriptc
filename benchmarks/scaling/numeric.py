"""Scaling probes that fit gates-v1.

scriptc's benchmarks/scaling drives arrays, strings, collections, misc, and
async IO, and reads an in-process T=<ms> from stderr. This file is the slice
that lowers: integer loops, floor division, a float recurrence, and string
concatenation. The runner times wall clock, because this subset has no clock.
"""


def sum_squares(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + i * i
        i = i + 1
    return total


def floor_sum(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + i // 3
        i = i + 1
    return total


def horner(n: int) -> float:
    acc: float = 0.0
    i: int = 0
    while i < n:
        acc = acc * 0.5 + 1.0
        i = i + 1
    return acc


def repeat_len(n: int) -> int:
    piece: str = "ab"
    text: str = ""
    i: int = 0
    while i < n:
        text = text + piece
        i = i + 1
    return len(text)
