def pick(x: int, y: int) -> int:
    ok = x < y
    if ok:
        return 1
    return 0


def main() -> None:
    print(pick(1, 2))


if __name__ == "__main__":
    main()
