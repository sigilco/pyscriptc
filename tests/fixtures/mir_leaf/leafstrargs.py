def wrap(s: str) -> str:
    return s


def pick(a: str, b: str) -> str:
    # Str arguments through a same-module call: a constant, a name and
    # a call result, in the direct emitter's left-to-right order.
    if a == "pre":
        return wrap(a)
    return wrap(b)


def go(n: int) -> str:
    out = pick("pre", wrap("mid"))
    i = 0
    while i < n:
        out = pick(out, "x")
        i = i + 1
    return out
