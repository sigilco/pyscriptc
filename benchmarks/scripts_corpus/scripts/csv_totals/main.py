"""Sum numeric CSV columns and print per-column totals."""

import csv
import sys


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: csv_totals FILE\n")
        return 2
    totals: dict[str, float] = {}
    count: int = 0
    header: list[str] = []
    with open(sys.argv[1]) as handle:
        for row in csv.reader(handle):
            if not header:
                header = row
                continue
            count = count + 1
            idx: int = 0
            for cell in row:
                if cell != "" and idx < len(header):
                    key: str = header[idx]
                    try:
                        number: float = float(cell)
                    except ValueError:
                        number = 0.0
                    totals[key] = totals.get(key, 0.0) + number
                idx = idx + 1
    for key in sorted(totals.keys()):
        print(f"{key:>12} {totals[key]:>12.2f}")
    print(f"{count} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
