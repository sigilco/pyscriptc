"""The word counter."""

import sys
from pathlib import Path

from counts import count_words, top

USAGE = "usage: word_count_pkg FILE [TOP]"


def main() -> int:
    argv: list[str] = sys.argv[1:]
    if len(argv) < 1:
        sys.stderr.write(USAGE + "\n")
        return 2
    limit: int = 3
    if len(argv) > 1:
        limit = int(argv[1])
    text: str = Path(argv[0]).read_text()
    counts: dict[str, int] = count_words(text)
    for word in top(counts, limit):
        number: int = counts[word]
        print(f"{word}: {number}")
    return 0
