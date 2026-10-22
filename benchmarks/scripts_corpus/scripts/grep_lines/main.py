"""Print the lines of a file that match a regular expression."""

import re
import sys


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: grep_lines PATTERN FILE\n")
        return 2
    pattern: str = sys.argv[1]
    hits: int = 0
    with open(sys.argv[2]) as handle:
        for line in handle:
            if re.search(pattern, line):
                hits = hits + 1
                sys.stdout.write(line)
    sys.stderr.write(f"{hits} matching lines\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
