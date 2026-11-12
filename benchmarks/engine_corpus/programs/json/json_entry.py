"""The typed entry over the json island module."""

import json_island


def main() -> None:
    a: str = json_island.dumps_sorted()
    b: int = json_island.loads_int()
    c: str = json_island.dumps_unicode()
    print(a)
    print(b)
    print(c)


if __name__ == "__main__":
    main()
