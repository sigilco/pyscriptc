"""Print the first N and last N lines of a file."""

import sys


def read_lines(path: str) -> list[str]:
    lines: list[str] = []
    with open(path) as handle:
        for line in handle:
            lines.append(line.rstrip("\n"))
    return lines


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: head_tail FILE N\n")
        return 2
    count: int = int(sys.argv[2])
    lines: list[str] = read_lines(sys.argv[1])
    if count < len(lines):
        head: list[str] = lines[:count]
        tail: list[str] = lines[len(lines) - count :]
    else:
        head = lines
        tail = lines
    for line in head:
        print(line)
    if len(head) != len(tail):
        print("...")
    for line in tail:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
