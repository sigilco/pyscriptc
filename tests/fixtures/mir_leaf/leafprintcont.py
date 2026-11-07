def show(items: list[int], words: list[str], table: dict[int, int], bag: set[int]) -> None:
    # print() of a container rewrites the argument to its repr str
    # (`ps_seq_repr`/`ps_map_repr`); the kind tags ride the call since a
    # PsSeq cell is only a byte size.
    print(items)
    print(words)
    print(table)
    print(bag)
    print([1, 2], {"k": 1}, items)


def show_nested(grid: list[list[int]], table: dict[str, list[int]], bags: list[set[str]]) -> None:
    # Nested containers ride the same repr calls: the spec string the
    # abi call carries recurses through `l`/`t`/`d` element specs.
    print(grid)
    print(table)
    print(bags)
    print([{"k": [1, 2]}], {"n": {"m": {1: [2.5]}}})


def main() -> None:
    show([1, 2], ["a"], {1: 2}, {3})
    show_nested([[1, 2], [3]], {"a": [1, 2], "b": []}, [{"x"}, set()])


if __name__ == "__main__":
    main()
