def fib(n: int) -> int:
    # A self-call: the callee table admits the caller's own record, so
    # the recursive calls lower to `MIR_CALL` like any other.
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)


def is_even(n: int) -> bool:
    # Mutual recursion: `is_even` and `is_odd` admit each other through
    # the module-level fixpoint.
    if n == 0:
        return True
    return is_odd(n - 1)


def is_odd(n: int) -> bool:
    if n == 0:
        return False
    return is_even(n - 1)


def main() -> None:
    print(fib(10), is_even(10), is_odd(7))


if __name__ == "__main__":
    main()
