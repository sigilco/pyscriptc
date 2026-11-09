def sum_range(n: int) -> int:
    total = 0
    for i in range(n):
        total = total + i
    return total


def sum_window(start: int, stop: int) -> int:
    total = 0
    for i in range(start, stop):
        total = total + i
    return total


def sum_stepped(start: int, stop: int) -> int:
    # A constant nonzero step: the desugar keeps both the up and the down
    # loop, so the `.ll` carries the sign branch even though the step is
    # known at compile time.
    total = 0
    for i in range(start, stop, 2):
        total = total + i
    return total


def main() -> None:
    print(sum_range(5), sum_window(2, 5), sum_stepped(1, 7))


if __name__ == "__main__":
    main()
