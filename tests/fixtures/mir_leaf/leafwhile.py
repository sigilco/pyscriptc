def sum_upto(n: int) -> int:
    total = 0
    i = 1
    while i <= n:
        total = total + i
        i = i + 1
    return total


def checked_accum(limit: int) -> int:
    # The body runs checked `w` arithmetic (the `ps.iadd` helper), so the
    # loop's loads and calls must land in the source order the direct
    # walk produces on every iteration.
    acc = 0
    k = 1
    while k < limit:
        acc = acc + k * 3
        k = k + 1
    return acc


def main() -> None:
    print(sum_upto(5), checked_accum(4))


if __name__ == "__main__":
    main()
