# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "toolz>=0.12.0",
# ]
# ///
from __future__ import annotations

from typing import Any

import toolz


def analyze_words(words: list[str]) -> dict[str, int]:
    freq: dict[str, int] = toolz.frequencies(words)
    return freq


def combine_stats(dict1: dict[str, int], dict2: dict[str, int]) -> dict[str, int]:
    combined: dict[str, int] = toolz.merge_with(sum, dict1, dict2)
    return combined


def main() -> None:
    sample: list[str] = ["apple", "banana", "apple", "cherry", "banana", "apple"]
    counts: dict[str, int] = analyze_words(sample)
    print("Counts:", sorted(counts.items()))

    extra: dict[str, int] = {"banana": 3, "date": 1}
    total: dict[str, int] = combine_stats(counts, extra)
    print("Total:", sorted(total.items()))

    partitions: list[tuple[Any, ...]] = list(toolz.partition(2, [1, 2, 3, 4]))
    print("Partitions:", partitions)


if __name__ == "__main__":
    main()
