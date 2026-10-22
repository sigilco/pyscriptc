"""Convert a number given on the command line into other units."""

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="argparse_convert")
    parser.add_argument("value", type=float, help="a length in metres")
    parser.add_argument("--unit", choices=("km", "mi", "ft"), default="km")
    args = parser.parse_args()
    if args.unit == "km":
        result: float = args.value / 1000.0
    elif args.unit == "mi":
        result = args.value / 1609.344
    else:
        result = args.value * 3.280839895
    print(f"{result:.3f} {args.unit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
