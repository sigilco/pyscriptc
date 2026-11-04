def flip(x: int) -> int:
    return -x


def net(a: int, b: int) -> int:
    return -a + b


def const_neg() -> int:
    return -1


def main() -> None:
    print(flip(1), net(3, 4), const_neg())


if __name__ == "__main__":
    main()
