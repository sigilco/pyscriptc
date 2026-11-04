def g(a: int, b: int) -> int:
    return a * b + 1


def main() -> None:
    xs = [1, 2, 3]
    total = 0
    for x in xs:
        total = total + g(x, 2)
    print(total)


if __name__ == "__main__":
    main()
