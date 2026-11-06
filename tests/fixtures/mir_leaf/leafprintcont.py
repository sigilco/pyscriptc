def show(items: list[int], words: list[str], table: dict[int, int], bag: set[int]) -> None:
    # print() of a container rewrites the argument to its repr str
    # (`ps_seq_repr`/`ps_map_repr`); the kind tags ride the call since a
    # PsSeq cell is only a byte size.
    print(items)
    print(words)
    print(table)
    print(bag)
    print([1, 2], {"k": 1}, items)


def main() -> None:
    show([1, 2], ["a"], {1: 2}, {3})


if __name__ == "__main__":
    main()
