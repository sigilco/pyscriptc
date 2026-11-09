def accum(limit: int) -> int:
    total = 0
    i = 0
    while True:
        i = i + 1
        if i > limit:
            break
        if i % 2 == 0:
            continue
        total = total + i
    return total


def checked_accum(limit: int) -> int:
    # The body runs checked `w` arithmetic (the `ps.iadd` helper) across
    # the loop, so the loads and calls must land in the source order the
    # direct walk produces through the break and continue guards.
    acc = 0
    k = 0
    while k < limit:
        k = k + 1
        if k % 3 == 0:
            continue
        if k > limit - 2:
            break
        acc = acc + k * 2
    return acc


def main() -> None:
    print(accum(10), checked_accum(8))


if __name__ == "__main__":
    main()
