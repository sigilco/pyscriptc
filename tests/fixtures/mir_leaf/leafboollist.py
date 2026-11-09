def mark(flags: list[bool], i: int, v: bool) -> None:
    # Bool cells through `ps_seq_set`: the i1 element width the
    # `list[bool]` slice admits.
    flags[i] = v


def count_true(flags: list[bool]) -> int:
    # Subscript reads widen the i1 cell; `not` on the bool result.
    n = 0
    i = 0
    while i < len(flags):
        if flags[i]:
            n = n + 1
        i = i + 1
    return n


def sieve_like(n: int) -> list[int]:
    # `[True] * (n + 1)` repeat plus a filtered comprehension over the
    # bool flags, the shape `sieve` uses.
    flags = [True] * (n + 1)
    flags[0] = False
    flags[1] = False
    i = 2
    while i * i <= n:
        if flags[i]:
            j = i * i
            while j <= n:
                flags[j] = False
                j = j + i
        i = i + 1
    return [k for k in range(n + 1) if flags[k]]


def main() -> None:
    print(len(sieve_like(10)))
    print(count_true([True, False, True]))


if __name__ == "__main__":
    main()
