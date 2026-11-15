# Issue #1226: the tagged dynamic value. `dict[str, object]` compiles; the
# values box into the runtime cell; the `in` test, the 1-arg and 2-arg
# `get`, and the `isinstance` tag test run next to CPython.
def handle(msg: dict[str, object]) -> int:
    total = 0
    for k in msg:
        v = msg.get(k)
        if isinstance(v, int):
            total = total + 1
        if isinstance(v, str):
            total = total + 10
        if isinstance(v, float):
            total = total + 100
    return total


print(handle({"a": 1, "b": "two", "c": 2.5, "d": True, "e": None}))
print(handle({}))


def present(msg: dict[str, object]) -> int:
    if "x" in msg:
        return 1
    return 0


print(present({"x": 1}), present({}))


def default(msg: dict[str, object]) -> int:
    # The 2-arg get's default arm is a concrete value; the read's type is
    # the sum, so the tag test is how the arm is seen.
    v = msg.get("missing", "fallback")
    if isinstance(v, str):
        return 1
    return 0


print(default({"a": 1}), default({"missing": 9}))


def passthrough(msg: dict[str, object]) -> int:
    n = 0
    for _k in msg:
        n = n + 1
    return n


print(passthrough({"p": 1, "q": "s", "r": [1, 2], "t": {"u": 3}}))
