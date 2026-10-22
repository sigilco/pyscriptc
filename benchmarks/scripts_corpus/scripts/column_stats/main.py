"""Compute min, max, mean and sum of each numeric column of a CSV file."""

import csv
import sys


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: column_stats FILE\n")
        return 2
    mins: dict[str, float] = {}
    maxs: dict[str, float] = {}
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    header: list[str] = []
    with open(sys.argv[1]) as handle:
        for row in csv.reader(handle):
            if not header:
                header = row
                continue
            idx: int = 0
            for cell in row:
                if cell != "" and idx < len(header):
                    try:
                        value: float = float(cell)
                    except ValueError:
                        idx = idx + 1
                        continue
                    key: str = header[idx]
                    if key not in mins or value < mins[key]:
                        mins[key] = value
                    if key not in maxs or value > maxs[key]:
                        maxs[key] = value
                    sums[key] = sums.get(key, 0.0) + value
                    counts[key] = counts.get(key, 0) + 1
                idx = idx + 1
    for key in sorted(mins.keys()):
        mean: float = sums[key] / float(counts[key])
        print(f"{key}: min {mins[key]:.2f} max {maxs[key]:.2f} mean {mean:.2f} sum {sums[key]:.2f} n {counts[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
