def bump(n: int) -> int:
    # Every augmented op the slice lowers on scalars: `+=` and `-=` go
    # through the checked helpers the plain binary form uses, `//=` and
    # `%=` through their runtime helpers, and the loads land in the
    # source order the direct walk produces.
    total = n
    total += 1
    total -= 2
    total *= 3
    total //= 2
    total %= 1000000000000
    return total


def accumulate(amount: int, ticks: int) -> int:
    acc = 0
    acc += amount
    acc -= ticks
    acc *= 2
    return acc


def main() -> None:
    print(bump(7), accumulate(10, 3))


if __name__ == "__main__":
    main()
