def twice(x: int) -> int:
    return x * 2


def positive(x: int) -> bool:
    return x > 0


def main() -> None:
    # The none-typed `print` builtin: scalar args only (int, float,
    # bool), default sep/end, stdout. Every spelling the direct
    # `_print_one` walk has for a scalar rides here: constants, names,
    # call results, and the multi-argument separator.
    print()
    print(1)
    print(2.5)
    print(True)
    print(False)
    n = 3
    f = 0.5
    b = n > 1
    print(n, f, b)
    print(twice(4), positive(2), 7)
    print(-3, not False)
    i = 0
    while i < 2:
        # A float print inside a loop body: the `%ps.str` alloca hoists
        # into the entry block, the direct `_alloca` spelling.
        print(1.5, i)
        i = i + 1
    if b:
        print(False)
    else:
        print(0)


if __name__ == "__main__":
    main()
