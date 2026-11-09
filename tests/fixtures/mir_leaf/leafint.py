def add(a: int, b: int) -> int:
    return a + b


def sub3(a: int, b: int, c: int) -> int:
    return a - b - c


def pick(x: int, lo: int) -> int:
    if x < lo:
        return lo
    return x


def main() -> None:
    print(pick(add(3, 4), sub3(9, 1, 2)))


if __name__ == "__main__":
    main()
