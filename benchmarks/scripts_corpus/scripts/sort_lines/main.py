"""Print the lines of a file in sorted order."""

import sys


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: sort_lines FILE\n")
        return 2
    lines: list[str] = []
    with open(sys.argv[1]) as handle:
        for line in handle:
            lines.append(line.rstrip("\n"))
    for line in sorted(lines):
        print(line)
    print(f"{len(lines)} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
