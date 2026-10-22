"""Keep the newest N lines per group in a log file."""

import sys

KEEP: int = 2


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: log_latest FILE\n")
        return 2
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    with open(sys.argv[1]) as handle:
        for line in handle:
            key: str = line.split(" ", 1)[0]
            if key not in groups:
                groups[key] = []
                order.append(key)
            entries: list[str] = groups[key]
            entries.append(line.rstrip("\n"))
            if len(entries) > KEEP:
                entries.pop(0)
    for key in order:
        for entry in groups[key]:
            print(entry)
    print(f"{len(order)} groups")
    return 0


if __name__ == "__main__":
    sys.exit(main())
