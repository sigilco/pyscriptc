"""Parse a combined access log and print status-code counts plus top paths."""

import re
import sys
from collections import Counter

LINE_RE: re.Pattern[str] = re.compile(r"^(\S+) \S+ \S+ \[([^\]]+)\] \"(\w+) (\S+) [^\"]+\" (\d{3}) (\d+)")
PATH_RE: re.Pattern[str] = re.compile(r"\?\S+$")


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: access_summary LOGFILE\n")
        return 2
    statuses: Counter[str] = Counter()
    paths: Counter[str] = Counter()
    hits: int = 0
    bad: int = 0
    with open(sys.argv[1]) as handle:
        for line in handle:
            match = LINE_RE.search(line)
            if match is None:
                bad = bad + 1
                continue
            hits = hits + 1
            status: str = match.group(5)
            raw_path: str = match.group(4)
            statuses[status] = statuses[status] + 1
            clean: str = PATH_RE.sub("", raw_path)
            paths[clean] = paths[clean] + 1
    print(f"{hits} requests, {bad} unparsed")
    for code in sorted(statuses.keys()):
        print(f"  {code} {statuses[code]}")
    print("top paths:")
    for path in sorted(paths.keys(), key=lambda p: -paths[p])[:5]:
        print(f"  {paths[path]:>6} {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
