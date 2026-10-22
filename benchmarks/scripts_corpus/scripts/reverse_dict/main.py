"""Reverse the keys and values of a two column text file."""

import sys


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: reverse_dict FILE\n")
        return 2
    pairs: list[tuple[str, str]] = []
    with open(sys.argv[1]) as handle:
        for line in handle:
            parts: list[str] = line.rstrip("\n").split("=", 1)
            if len(parts) == 2:
                pairs.append((parts[0], parts[1]))
    for key, value in sorted(pairs, key=lambda pair: pair[1]):
        print(f"{value}={key}")
    print(f"{len(pairs)} pairs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
