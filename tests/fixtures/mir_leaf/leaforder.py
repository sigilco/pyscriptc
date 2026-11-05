def left_first(a: int, b: int) -> int:
    # The left operand's multiply is a checked helper, so it can raise:
    # the order the two operands are evaluated in is observable, and the
    # MIR lane has to evaluate the left one first.
    return a * 1000000000000 + b * 2


def nested_right(a: int, b: int, c: int) -> int:
    # A computed right operand against a plain left one: the loads and
    # the calls have to land in the source order the direct walk uses.
    return a + b * c * 2


def main() -> None:
    print(left_first(2, 3), nested_right(1, 2, 3))


if __name__ == "__main__":
    main()
