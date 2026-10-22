"""Pretty-print a JSON document with sorted keys and 2-space indent."""

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: json_pretty FILE\n")
        return 2
    text: str = Path(sys.argv[1]).read_text()
    document: dict[str, list[str]] = json.loads(text)
    print(json.dumps(document, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
