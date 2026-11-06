def cat(a: str, b: str) -> str:
    return a + b


def catmix(a: str, b: str) -> str:
    # Concat with constants on both sides of a name read.
    return "pre-" + a + "-" + b + "!"


def order(a: str, b: str) -> int:
    # The four ordering predicates through `ps_str_cmp`.
    if a < b:
        return 1
    if a <= b:
        return 2
    if a > b:
        return 3
    if a >= b:
        return 4
    return 0


def has(s: str, n: str) -> bool:
    return n in s


def lacks(s: str) -> bool:
    return "x" not in s


def mix(s: str) -> bool:
    # Concat feeding a compare feeding a contains, all in one expression.
    return s + "y" == "ay" and "b" in s


def build(n: int) -> str:
    # Concat inside a loop body: the strconcat call per iteration.
    out = ""
    i = 0
    while i < n:
        out = out + "x"
        i = i + 1
    return out
