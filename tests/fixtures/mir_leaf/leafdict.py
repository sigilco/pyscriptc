def keys(d: dict[int, int]) -> int:
    return len(d.keys())


def build() -> int:
    d: dict[int, int] = {1: 2, 3: 4}
    return len(d.keys())


def main() -> None:
    print(keys({5: 6}), build())


if __name__ == "__main__":
    main()
