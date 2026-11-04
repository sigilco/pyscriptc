def q(a: int) -> int:
    return a // a


def r(a: int) -> int:
    return a % a


def mul(a: int, b: int) -> int:
    z = a * a
    return a * b


def main() -> None:
    print(q(6), r(6), mul(2, 3))


if __name__ == "__main__":
    main()
