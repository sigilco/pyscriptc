"""Render an ASCII bar chart of word counts."""

import sys

BAR_MAX: int = 40


def bar(count: int, top: int) -> str:
    if top == 0:
        return ""
    width: int = count * BAR_MAX // top
    return "#" * width


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: bar_chart FILE\n")
        return 2
    counts: dict[str, int] = {}
    with open(sys.argv[1]) as handle:
        for line in handle:
            for word in line.split():
                counts[word] = counts.get(word, 0) + 1
    words: list[str] = sorted(counts.keys())
    top: int = 0
    for word in words:
        if counts[word] > top:
            top = counts[word]
    for word in sorted(words, key=lambda w: -counts[w])[:10]:
        print(f"{word:>12} {counts[word]:>4} {bar(counts[word], top)}")
    print(f"{len(words)} words, max {top}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
