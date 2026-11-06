def third(i: int, j: int) -> float:
    return 1.0 / ((i + j) * (i + j + 1) // 2 + i + 1)


def main() -> None:
    print(third(3, 4))


if __name__ == "__main__":
    main()
