from __future__ import annotations

import sys


def main() -> None:
    line: str = sys.stdin.readline()
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
