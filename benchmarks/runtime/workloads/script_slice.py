"""A short script: json, a regex, and a pathlib name. No file and no island."""

import json
import pathlib
import re


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        row: dict[str, int] = json.loads('{"id": 12, "n": 3}')
        hits: list[str] = re.findall(r"\d+", "id=12;n=3")
        path: pathlib.Path = pathlib.Path("logs/app.txt")
        parent: pathlib.Path = path.parent
        total = total + row["id"] + len(hits[0]) + len(path.suffix) + len(parent.as_posix())
        i = i + 1
    return total
