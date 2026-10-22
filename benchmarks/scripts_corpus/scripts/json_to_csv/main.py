"""Turn a JSON array of flat objects into CSV rows."""

import csv
import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: json_to_csv FILE\n")
        return 2
    text: str = Path(sys.argv[1]).read_text()
    records: list[dict[str, str]] = json.loads(text)
    columns: list[str] = []
    for record in records:
        for key in record.keys():
            if key not in columns:
                columns.append(key)
    columns.sort()
    writer = csv.writer(sys.stdout)
    writer.writerow(columns)
    for record in records:
        row: list[str] = []
        for key in columns:
            row.append(record.get(key, ""))
        writer.writerow(row)
    sys.stderr.write(f"{len(records)} records, {len(columns)} columns\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
