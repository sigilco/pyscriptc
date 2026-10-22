"""Join two CSV files on a key column."""

import csv
import sys


def load(path: str) -> tuple[list[str], list[list[str]]]:
    header: list[str] = []
    rows: list[list[str]] = []
    with open(path) as handle:
        for row in csv.reader(handle):
            if not header:
                header = row
                continue
            rows.append(row)
    return header, rows


def main() -> int:
    if len(sys.argv) != 4:
        sys.stderr.write("usage: csv_join LEFT.csv RIGHT.csv KEY\n")
        return 2
    key: str = sys.argv[3]
    left_header, left_rows = load(sys.argv[1])
    right_header, right_rows = load(sys.argv[2])
    left_index: int = left_header.index(key)
    right_index: int = right_header.index(key)
    lookup: dict[str, list[str]] = {}
    for row in right_rows:
        if right_index < len(row):
            lookup[row[right_index]] = row
    joined: int = 0
    writer = csv.writer(sys.stdout)
    writer.writerow(left_header + [name for name in right_header if name != key])
    for row in left_rows:
        if left_index >= len(row):
            continue
        match: list[str] = []
        if row[left_index] in lookup:
            other: list[str] = lookup[row[left_index]]
            idx: int = 0
            for name in right_header:
                if name != key and idx < len(other):
                    match.append(other[idx])
                idx = idx + 1
            joined = joined + 1
        writer.writerow(row + match)
    sys.stderr.write(f"{joined} of {len(left_rows)} rows joined\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
