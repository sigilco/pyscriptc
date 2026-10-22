"""Compare two text files line by line."""

import sys


def read_lines(path: str) -> list[str]:
    lines: list[str] = []
    with open(path) as handle:
        for line in handle:
            lines.append(line.rstrip("\n"))
    return lines


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: diff_lines FILE_A FILE_B\n")
        return 2
    left: list[str] = read_lines(sys.argv[1])
    right: list[str] = read_lines(sys.argv[2])
    left_only: list[str] = []
    right_only: list[str] = []
    idx: int = 0
    while idx < len(left) and idx < len(right):
        if left[idx] != right[idx]:
            left_only.append(left[idx])
            right_only.append(right[idx])
        idx = idx + 1
    while idx < len(left):
        left_only.append(left[idx])
        idx = idx + 1
    while idx < len(right):
        right_only.append(right[idx])
        idx = idx + 1
    for line in left_only:
        print(f"- {line}")
    for line in right_only:
        print(f"+ {line}")
    print(f"{len(left_only)} removed, {len(right_only)} added")
    return 0


if __name__ == "__main__":
    sys.exit(main())
