"""Turn a CSV file into a JSON array of objects."""

import csv
import json
import sys


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: csv_to_json FILE\n")
        return 2
    records: list[dict[str, str]] = []
    header: list[str] = []
    with open(sys.argv[1]) as handle:
        for row in csv.reader(handle):
            if not header:
                header = row
                continue
            record: dict[str, str] = {}
            idx: int = 0
            for cell in row:
                if idx < len(header):
                    record[header[idx]] = cell
                idx = idx + 1
            records.append(record)
    print(json.dumps(records, sort_keys=True))
    sys.stderr.write(f"{len(records)} records\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
