def eq_left(s: str) -> bool:
    # A str constant before a name read: the constant's materialization
    # must land before the slot widen, the direct emitter's order.
    return "a" == s


def eq_right(s: str) -> bool:
    return s == "a"


def ne_mixed(a: str, b: str) -> bool:
    return "x" != a or b == "y"
