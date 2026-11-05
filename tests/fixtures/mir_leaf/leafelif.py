def pick(a: int) -> int:
    # A three-way `elif` chain lowers as nested if/else: each arm's join
    # block is the next arm's header, and the label numbers follow the
    # direct emitter's pre-order `fresh()` sequence, not the block
    # construction order.
    if a < 0:
        x = 1
    elif a < 5:
        x = 2
    elif a < 10:
        x = 3
    else:
        x = 4
    return x


def grade(score: int) -> int:
    # The arms run checked arithmetic, so the loads and calls through the
    # chain must stay in the source order the direct walk produces.
    if score < 50:
        band = score * 2
    elif score < 75:
        band = score + 100
    else:
        band = score - 1
    return band


def main() -> None:
    print(pick(-1), pick(3), pick(7), pick(20), grade(10), grade(60), grade(90))


if __name__ == "__main__":
    main()
