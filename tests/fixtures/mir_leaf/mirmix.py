def g(a: int, b: int) -> int:
    return a * b + 1


def main() -> None:
    xs = [1, 2, 3]
    total = 0
    for x in xs:
        total = total + g(x, 2)
    # `min` is outside the call slice, so `main` rides the direct path
    # while `g` stays the in-slice half of the strangler sample.
    print("total", total, min(1, 2))


if __name__ == "__main__":
    main()
