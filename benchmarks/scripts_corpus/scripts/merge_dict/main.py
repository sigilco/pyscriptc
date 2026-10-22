"""Merge two key=value files, reporting the conflicts."""

import sys


def load(path: str) -> dict[str, str]:
    table: dict[str, str] = {}
    with open(path) as handle:
        for line in handle:
            parts: list[str] = line.rstrip("\n").split("=", 1)
            if len(parts) == 2:
                table[parts[0]] = parts[1]
    return table


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: merge_dict FILE_A FILE_B\n")
        return 2
    left: dict[str, str] = load(sys.argv[1])
    right: dict[str, str] = load(sys.argv[2])
    conflicts: int = 0
    for key in sorted(left.keys()):
        if key in right and right[key] != left[key]:
            conflicts = conflicts + 1
            print(f"conflict {key}: {left[key]} != {right[key]}")
    merged: dict[str, str] = {}
    for key in left.keys():
        merged[key] = left[key]
    for key in right.keys():
        if key not in merged:
            merged[key] = right[key]
    for key in sorted(merged.keys()):
        print(f"{key}={merged[key]}")
    print(f"{len(merged)} keys, {conflicts} conflicts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
