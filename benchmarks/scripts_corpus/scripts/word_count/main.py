"""Count words in a text file and print the top N by frequency."""

import sys
from pathlib import Path

PUNCT: str = ".,;:!?\"'()[]"


def count_words(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for word in text.lower().split():
        cleaned: str = word.strip(PUNCT)
        if not cleaned:
            continue
        counts[cleaned] = counts.get(cleaned, 0) + 1
    return counts


def top(counts: dict[str, int], limit: int) -> list[str]:
    ranked: list[str] = sorted(counts.keys(), key=lambda word: -counts[word])
    return ranked[:limit]


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: word_count FILE [LIMIT]\n")
        return 2
    limit: int = 10
    if len(sys.argv) > 2:
        limit = int(sys.argv[2])
    text: str = Path(sys.argv[1]).read_text()
    counts: dict[str, int] = count_words(text)
    for word in top(counts, limit):
        print(f"{counts[word]:>6} {word}")
    print(f"{len(counts)} distinct words")
    return 0


if __name__ == "__main__":
    sys.exit(main())
