# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "more-itertools>=10.0.0",
# ]
# ///
from __future__ import annotations

import more_itertools


def process_numbers(data: list[int]) -> list[list[int]]:
    chunks: list[list[int]] = list(more_itertools.chunked(data, 3))
    return chunks


def summarize_windows(items: list[int]) -> list[int]:
    result: list[int] = []
    for w in more_itertools.windowed(items, 2):
        if w[0] is not None and w[1] is not None:
            result.append(int(w[0]) + int(w[1]))
    return result


def main() -> None:
    nums: list[int] = [1, 2, 3, 4, 5, 6, 7]
    chunks: list[list[int]] = process_numbers(nums)
    print("Chunks count:", len(chunks))
    for c in chunks:
        print("Chunk:", c)
    windows: list[int] = summarize_windows(nums)
    print("Window sums:", windows)
    first_val: int = more_itertools.first(nums, -1)
    print("First:", first_val)


if __name__ == "__main__":
    main()
