def pick_true(x: int) -> bool:
    if x < 3:
        return True
    return False


def flip(x: int) -> bool:
    return not True


def main() -> None:
    print(pick_true(1), flip(2))


if __name__ == "__main__":
    main()
