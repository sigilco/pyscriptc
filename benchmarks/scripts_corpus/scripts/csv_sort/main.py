"""Sort the rows of a CSV file by one column."""

import csv
import sys


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: csv_sort FILE COLUMN\n")
        return 2
    column: int = int(sys.argv[2])
    header: list[str] = []
    rows: list[list[str]] = []
    with open(sys.argv[1]) as handle:
        for row in csv.reader(handle):
            if not header:
                header = row
                continue
            rows.append(row)
    ordered: list[list[str]] = sorted(rows, key=lambda row: row[column] if column < len(row) else "")
    writer = csv.writer(sys.stdout)
    writer.writerow(header)
    for row in ordered:
        writer.writerow(row)
    sys.stderr.write(f"{len(rows)} rows sorted by column {column}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
