"""json.loads and json.dumps of an object of ints."""

import json


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    raw: str = '{"id": 12, "n": 3}'
    while i < n:
        row: dict[str, int] = json.loads(raw)
        text: str = json.dumps(row)
        total = total + row["id"] + len(text)
        i = i + 1
    return total
