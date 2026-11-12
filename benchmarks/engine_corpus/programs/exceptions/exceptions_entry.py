"""The typed entry over the exceptions island module."""

import exceptions_island


def main() -> None:
    a: str = exceptions_island.caught()
    b: str = exceptions_island.chained()
    c: str = exceptions_island.custom()
    print(a)
    print(b)
    print(c)


if __name__ == "__main__":
    main()
