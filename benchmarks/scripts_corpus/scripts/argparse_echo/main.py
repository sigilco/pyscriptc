"""Echo the parsed command line arguments."""

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="argparse_echo", description="Echo parsed arguments.")
    parser.add_argument("name", help="a name to greet")
    parser.add_argument("--count", type=int, default=1, help="how many times")
    parser.add_argument("--upper", action="store_true", help="uppercase the name")
    args = parser.parse_args()
    for _ in range(args.count):
        if args.upper:
            print(args.name.upper())
        else:
            print(args.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
