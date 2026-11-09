def mixed(xs: list[int]) -> int:
    total = 0
    for a, b in zip("abc", xs):  # noqa: B905
        total = total + len(a) + b
    return total


def scanned(text: str) -> int:
    n = 0
    for i, c in enumerate(text, 1):
        n = n + i + len(c)
    return n


def triple(a: list[int], b: list[int], c: list[int]) -> int:
    total = 0
    for x, y, z in zip(a, b, c):  # noqa: B905
        total = total + x + y + z
    return total


def main() -> None:
    print(mixed([1, 2, 3]))
    print(scanned("xax"))
    print(triple([1, 2], [3, 4], [5, 6]))


if __name__ == "__main__":
    main()
