"""Print the distinct lines of a file with a repeat count."""

import sys


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: unique_lines FILE\n")
        return 2
    counts: dict[str, int] = {}
    order: list[str] = []
    with open(sys.argv[1]) as handle:
        for line in handle:
            key: str = line.rstrip("\n")
            if key not in counts:
                counts[key] = 0
                order.append(key)
            counts[key] = counts.get(key, 0) + 1
    for key in order:
        print(f"{counts[key]:>5} {key}")
    print(f"{len(order)} distinct")
    return 0


if __name__ == "__main__":
    sys.exit(main())
