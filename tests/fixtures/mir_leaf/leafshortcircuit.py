def twice(v: int) -> int:
    return v * 2


def land_int(a: int, b: int) -> int:
    # `a or b` on same-type ints: the temp is the deciding operand, and
    # the untaken side's load only runs when the left did not decide it.
    return a or b


def land_and(a: int, b: int) -> int:
    return a and b


def land_chain(a: int, b: int, c: int) -> int:
    # A three-operand chain nests: each join feeds the next header.
    return a or b or c


def land_float(a: float, b: float) -> float:
    return a and b


def land_bool(a: bool, b: bool) -> bool:
    return a or b


def land_mixed(a: int, b: int) -> bool:
    # The compare sides make the result a bool: the slice's compare ops
    # already produce i1, so the `and` joins two bool temps.
    return a > 0 and b > 0


def land_calls(x: int) -> int:
    # The call on the right side runs only when the left does not decide.
    return twice(x) and x + 1


if __name__ == "__main__":
    print(
        land_int(1, 0),
        land_and(1, 2),
        land_chain(0, 2, 3),
        land_float(1.0, 0.0),
        land_bool(True, False),
        land_mixed(1, 2),
        land_calls(4),
    )
