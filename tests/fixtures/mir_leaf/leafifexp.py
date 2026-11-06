def half(v: int) -> int:
    return v // 2


def ifexp_int(a: int, b: int, flag: bool) -> int:
    return a if flag else b


def ifexp_float(a: float, b: float, flag: bool) -> float:
    return a if flag else b


def ifexp_bool(a: bool, b: bool, flag: bool) -> bool:
    return a if flag else b


def ifexp_cond_cmp(a: int, b: int) -> int:
    # The condition is a compare, not a bare bool name.
    return a if a > b else b


def ifexp_nested(a: int, b: int, flag: bool) -> int:
    return (a if flag else b) if flag else 0


def ifexp_calls(a: int, flag: bool) -> int:
    # The untaken branch's call does not run: each operand sits in its
    # own arm, the way the direct path lowers the desugared if.
    return half(a) if flag else 0


if __name__ == "__main__":
    print(ifexp_int(1, 2, True), ifexp_float(1.0, 2.0, False), ifexp_bool(True, False, True), ifexp_cond_cmp(3, 4), ifexp_nested(1, 2, True), ifexp_calls(6, True))
