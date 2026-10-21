"""Count lines, words and characters of a file."""

import sys


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: count_lines FILE\n")
        return 2
    lines: int = 0
    words: int = 0
    chars: int = 0
    with open(sys.argv[1]) as handle:
        for line in handle:
            lines = lines + 1
            chars = chars + len(line)
            words = words + len(line.split())
    print(f"lines {lines}")
    print(f"words {words}")
    print(f"chars {chars}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
