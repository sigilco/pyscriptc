def nlen(s: str) -> int:
    return len(s)


def nstr_i(n: int) -> str:
    return str(n)


def nstr_f(f: float) -> str:
    return str(f)


def nstr_b(b: bool) -> str:
    return str(b)


def nstr_s(s: str) -> str:
    # `str` of a str is the identity: no call is emitted.
    return str(s)


def nint_f(x: float) -> int:
    return int(x)


def nint_s(s: str) -> int:
    return int(s)


def nbin(x: int) -> str:
    return bin(x)


def nhex(x: int) -> str:
    return hex(x)


def noct(x: int) -> str:
    return oct(x)


def use(s: str, n: int) -> int:
    # The builtins in argument and result positions, mixed with a
    # same-module call: len feeds int, str feeds the call.
    total = len(s) + nint_f(2.5)
    out = nstr_s(s)
    return total + len(out) + n
