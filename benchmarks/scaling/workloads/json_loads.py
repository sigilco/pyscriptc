"""Workload program for json_loads scaling probe (#387)."""

from __future__ import annotations

import json


def bench(n: int) -> int:
    d: dict[str, int] = {}
    i: int = 0
    while i < n:
        d[str(i)] = i
        i = i + 1
    raw: str = json.dumps(d)
    parsed: dict[str, int] = json.loads(raw)
    return len(parsed)


def json_loads(n: int) -> int:
    return bench(n)
