def inc(x: int) -> int:
    return x + 1


def twice(a: int, b: int) -> int:
    return a + b


def sign(x: int) -> int:
    if x == 0:
        return 0
    return 1


def main() -> None:
    print(inc(1), twice(2, 3), sign(4))


if __name__ == "__main__":
    main()
