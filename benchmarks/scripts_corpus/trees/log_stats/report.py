"""The log summary."""

import sys

from parse import parse_message, parse_source
from reader import read_lines

USAGE = "usage: log_stats FILE"


def main() -> int:
    argv: list[str] = sys.argv[1:]
    if len(argv) < 1:
        sys.stderr.write(USAGE + "\n")
        return 2
    counts: dict[str, int] = {}
    total: int = 0
    last: str = ""
    for line in read_lines(argv[0]):
        if not line:
            continue
        total = total + 1
        source: str = parse_source(line)
        message: str = parse_message(line)
        counts[source] = counts.get(source, 0) + 1
        last = message
    ranked: list[str] = sorted(counts.keys())
    for source in ranked:
        print(f"{source}: {counts[source]}")
    print(f"total: {total}")
    print(f"last: {last}")
    return 0
