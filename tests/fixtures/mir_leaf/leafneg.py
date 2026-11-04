def flip(x: int) -> int:
    return -x


def net(a: int, b: int) -> int:
    return -a + b


def main() -> None:
    print(flip(1), net(3, 4))


if __name__ == "__main__":
    main()
