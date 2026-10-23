from collections.abc import Iterator


def gen(n: int) -> Iterator[int]:
    i: int = 0
    while i < n:
        yield i
        i += 1


def main() -> None:
    total: int = 0
    for v in gen(100_000_000):
        total += v
    print(total)


main()
