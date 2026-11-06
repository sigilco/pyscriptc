def third(i: int, j: int) -> float:
    return 1.0 / ((i + j) * (i + j + 1) // 2 + i + 1)


def negf(x: float) -> float:
    # Unary `-` on a float: `MIR_NEGD`, the `fneg` spelling.
    return -x


def negi(x: int) -> int:
    # Unary `-` on an int: `MIR_NEGW`, the checked `ps.ineg` helper.
    return -x


def main() -> None:
    print(third(3, 4))


if __name__ == "__main__":
    main()
