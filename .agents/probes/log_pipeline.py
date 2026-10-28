"""Data pipeline archetype probe: csv in, aggregated JSON out.

The shape a CPU-bound pipeline has in the wild: read a CSV, aggregate by key,
emit JSON. This is the probe the apps strategy measures the data archetype
with; see `.agents/decisions.md` (the package tiering policy).

Expected today: 0 fences, with two known gaps that make the shape awkward
rather than fatal:

  1. A bare `list` or `dict` annotation fences the local and every use of it
     (PS1106, then PS2002 name-resolution). The element types are spelled out
     here; that gap is issue #477.
  2. `for r in rows` over a `list` of user class instances fences with
     "for over list[Row] is not lowered" (PS2002). The lowerer accepts a
     scalar, a list or a dict element, or a tuple class, but not a plain
     class. The index loop below is the workaround; the fix is issue #489.

The index loop is the shape a real pipeline would have to write today, which
is the cost this probe is measuring.
"""

import csv
import json
from dataclasses import dataclass


@dataclass
class Row:
    key: str
    value: float


def load(path: str) -> list[Row]:
    out: list[Row] = []
    with open(path) as fh:
        for row in csv.reader(fh):
            if len(row) < 2:
                continue
            out.append(Row(row[0], float(row[1])))
    return out


def aggregate(rows: list[Row]) -> dict[str, float]:
    acc: dict[str, float] = {}
    i: int = 0
    while i < len(rows):
        r: Row = rows[i]
        cur: float = acc.get(r.key, 0.0)
        acc[r.key] = cur + r.value
        i = i + 1
    return acc


def emit(acc: dict[str, float]) -> str:
    parts: list[str] = []
    for k in acc:
        parts.append(k)
    return json.dumps(acc)


def main(path: str) -> str:
    rows: list[Row] = load(path)
    acc: dict[str, float] = aggregate(rows)
    return emit(acc)
