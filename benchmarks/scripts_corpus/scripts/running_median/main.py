"""Compute the running median of numbers in a file."""

import sys


def median_of(sorted_values: list[float]) -> float:
    count: int = len(sorted_values)
    mid: int = count // 2
    if count % 2 == 1:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2.0


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: running_median FILE\n")
        return 2
    seen: list[float] = []
    with open(sys.argv[1]) as handle:
        for line in handle:
            for token in line.split():
                value: float = float(token)
                seen.append(value)
                ordered: list[float] = sorted(seen)
                print(f"{len(seen):>4} {median_of(ordered):>10.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
