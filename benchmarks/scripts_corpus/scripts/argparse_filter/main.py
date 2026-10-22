"""Filter the lines of a file with a pattern given on the command line."""

import argparse
import re
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="argparse_filter")
    parser.add_argument("path", help="the file to read")
    parser.add_argument("--pattern", required=True, help="a regular expression")
    parser.add_argument("--invert", action="store_true", help="keep the non matching lines")
    args = parser.parse_args()
    kept: int = 0
    with open(args.path) as handle:
        for line in handle:
            matched: bool = re.search(args.pattern, line) is not None
            if matched != args.invert:
                kept = kept + 1
                sys.stdout.write(line)
    sys.stderr.write(f"{kept} lines kept\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
