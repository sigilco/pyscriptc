"""Drop duplicate lines from a file, keeping the first occurrence."""

import sys


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: dedupe_lines FILE\n")
        return 2
    seen: dict[str, int] = {}
    kept: list[str] = []
    with open(sys.argv[1]) as handle:
        for line in handle:
            key: str = line.rstrip("\n")
            if key not in seen:
                seen[key] = 1
                kept.append(key)
    for line in kept:
        print(line)
    print(f"{len(kept)} unique")
    return 0


if __name__ == "__main__":
    sys.exit(main())
