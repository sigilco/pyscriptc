# SPDX-License-Identifier: Apache-2.0
# Issue #415: an empty display in a conditional expression takes the other
# arm's type as its proof (`[data] if data else []`, iniconfig's
# `_parse.parse_ini_data`). Both arm orders lower, and the empty arm
# prints the way CPython prints the empty list.
def main() -> None:
    data = "x"
    lines: list[str] = [data] if data else []
    print(lines)
    other: list[str] = [] if data else [data]
    print(other)
    empty: list[str] = []
    print(empty)


if __name__ == "__main__":
    main()
