"""Extract one key from each record of a JSON array."""

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: json_select FILE KEY\n")
        return 2
    text: str = Path(sys.argv[1]).read_text()
    records: list[dict[str, str]] = json.loads(text)
    key: str = sys.argv[2]
    missing: int = 0
    for record in records:
        if key in record:
            print(record[key])
        else:
            missing = missing + 1
    sys.stderr.write(f"{len(records) - missing} values, {missing} missing\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
