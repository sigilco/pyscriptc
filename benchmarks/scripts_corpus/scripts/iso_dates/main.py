"""Report a table of ISO dates parsed from a text file."""

import re
import sys
from datetime import date

DATE_RE: re.Pattern[str] = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: iso_dates FILE\n")
        return 2
    found: int = 0
    mondays: int = 0
    earliest: date = date.max
    latest: date = date.min
    with open(sys.argv[1]) as handle:
        for line in handle:
            match: re.Match = DATE_RE.search(line)
            if match is None:
                continue
            parsed: date = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            found = found + 1
            if parsed.weekday() == 0:
                mondays = mondays + 1
            if parsed < earliest:
                earliest = parsed
            if parsed > latest:
                latest = parsed
    if found:
        print(f"found {found} dates")
        print(f"mondays {mondays}")
        print(f"earliest {earliest.isoformat()}")
        print(f"latest {latest.isoformat()}")
    else:
        print("no dates found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
