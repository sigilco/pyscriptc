"""Aggregate a small CSV of name,value pairs."""

from pathlib import Path


def main() -> int:
    path: Path = Path("values.csv")
    total: int = 0
    count: int = 0
    for line in path.read_text().splitlines():
        if len(line.strip()) == 0:
            continue
        parts: list[str] = line.split(",")
        if len(parts) != 2:
            continue
        total = total + int(parts[1])
        count = count + 1
    print("rows:", count)
    print("total:", total)
    print("mean:", total // count)
    return 0
