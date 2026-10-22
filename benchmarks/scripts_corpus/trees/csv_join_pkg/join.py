"""The join logic."""

import sys

from tables import read_csv, write_rows

USAGE = "usage: csv_join LEFT.csv RIGHT.csv COLUMN"


def main() -> int:
    argv: list[str] = sys.argv[1:]
    if len(argv) < 3:
        sys.stderr.write(USAGE + "\n")
        return 2
    left: list[list[str]] = read_csv(argv[0])
    right: list[list[str]] = read_csv(argv[1])
    column: str = argv[2]
    if not left or not right:
        sys.stderr.write("empty input\n")
        return 1
    header: list[str] = left[0]
    if column not in header:
        sys.stderr.write(f"no column {column}\n")
        return 1
    key_at: int = header.index(column)
    right_header: list[str] = right[0]
    if column not in right_header:
        sys.stderr.write(f"no column {column}\n")
        return 1
    right_at: int = right_header.index(column)
    lookup: dict[str, list[str]] = {}
    for row in right[1:]:
        key: str = row[right_at]
        lookup[key] = row
    keep: list[int] = []
    for position in range(len(right_header)):
        if position != right_at:
            keep.append(position)
    out_header: list[str] = list(header)
    for position in keep:
        out_header.append(right_header[position])
    rows: list[list[str]] = [out_header]
    for row in left[1:]:
        want: str = row[key_at]
        if want not in lookup:
            continue
        matched: list[str] = lookup[want]
        merged: list[str] = list(row)
        for position in keep:
            index: int = position
            merged.append(matched[index])
        rows.append(merged)
    write_rows(rows)
    return 0
