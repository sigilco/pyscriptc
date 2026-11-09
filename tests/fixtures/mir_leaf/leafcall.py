def add(a: int, b: int) -> int:
    return a + b


def scale(a: int, b: int) -> int:
    return add(a, add(b, 1))


def note(x: int) -> None:
    return


def total(x: int, y: int) -> int:
    # A void call between two value calls: the void call emits (its
    # effects are the call itself) and binds nothing, the way the direct
    # path emits the call then skips the store.
    note(x)
    acc = add(x, y)
    note(acc)
    return add(acc, scale(x, y))


def main() -> None:
    print(total(3, 4))


if __name__ == "__main__":
    main()
