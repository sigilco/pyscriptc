"""The matching logic."""

import re
import sys
from pathlib import Path

USAGE = "usage: grep_ctx PATTERN FILE"


def main() -> int:
    argv: list[str] = sys.argv[1:]
    if len(argv) < 2:
        sys.stderr.write(USAGE + "\n")
        return 2
    pattern: str = argv[0]
    found: int = 0
    number: int = 0
    text: str = Path(argv[1]).read_text()
    for line in text.split("\n"):
        number = number + 1
        if re.search(pattern, line):
            found = found + 1
            print(f"{number}: {line}")
    if found == 0:
        sys.stderr.write("no match\n")
        return 1
    return 0
