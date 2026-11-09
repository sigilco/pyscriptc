def gray(n: int) -> int:
    return n ^ (n >> 1)


def mask(n: int) -> int:
    return (n << 3) | 15


def invert(n: int) -> int:
    return ~n


def fold(a: int, b: int) -> int:
    return (a | b) ^ (a & b) ^ ~(a >> 2)


def main() -> None:
    print(gray(88), mask(5), invert(0), fold(12, 10), 6 >> 1)


if __name__ == "__main__":
    main()
