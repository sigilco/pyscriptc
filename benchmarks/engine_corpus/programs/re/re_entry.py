"""The typed entry over the re island module."""

import re_island


def main() -> None:
    a: str = re_island.match_groups()
    b: str = re_island.find_all()
    c: int = re_island.sub_count()
    print(a)
    print(b)
    print(c)


if __name__ == "__main__":
    main()
