"""The typed entry over the lists island module."""

import lists_island


def main() -> None:
    a: list[int] = lists_island.echo_ints([1, 2, 3])
    b: list[str] = lists_island.echo_strs(["a", "b"])
    c: list[int] = lists_island.empty()
    d: list[int] = lists_island.built(4)
    e: int = lists_island.total(a)
    print(len(a))
    print(len(b))
    print(len(c))
    print(len(d))
    print(e)


if __name__ == "__main__":
    main()
