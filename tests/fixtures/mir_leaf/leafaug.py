def aug_add(x: int) -> int:
    x += 3
    return x


def aug_sub(x: int) -> int:
    x -= 1
    return x


def aug_mul(x: int) -> int:
    x *= 2
    return x


def aug_floordiv(x: int) -> int:
    x //= 3
    return x


def aug_mod(x: int) -> int:
    x %= 5
    return x


def aug_float(x: float) -> float:
    x += 1.5
    x *= 2.0
    x /= 4.0
    return x


def aug_in_loop(n: int) -> int:
    # The `+=` reads and writes the same slot each iteration: the merged
    # loop is where the slot-shaped lowering earns its shape.
    c = 0
    while n > 0:
        n -= 1
        c += 1
    return c


def aug_shortcircuit(x: int) -> int:
    # `x += 1` desugars to the plain assign the slice lowers; the short-
    # circuit `or` on a scalar local folds the same way.
    x = x or 7
    return x


def main() -> None:
    print(
        aug_add(1),
        aug_sub(2),
        aug_mul(3),
        aug_floordiv(10),
        aug_mod(17),
        aug_float(2.0),
        aug_in_loop(4),
        aug_shortcircuit(0),
    )


if __name__ == "__main__":
    main()
