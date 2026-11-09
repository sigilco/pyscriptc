def popcount(n: int) -> int:
    c = 0
    while n != 0:
        n = n & (n - 1)
        c = c + 1
    return c


def main() -> None:
    print(popcount(255), 12 & 10)


if __name__ == "__main__":
    main()
