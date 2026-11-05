def count_upto(n: int) -> int:
    # `for i in range(n)` lowers to the desugared shape the direct path
    # emits: a step-zero guard that raises, an ascending while loop and a
    # descending one. The MIR lane has to land on the same header, body
    # and exit blocks, and the same label numbers, as the direct walk.
    total = 0
    for i in range(n):
        total = total + i
    return total


def count_between(a: int, b: int) -> int:
    # `range(a, b)` keeps the caller's start and stop.
    total = 0
    for i in range(a, b):
        total += i
    return total


def count_step(a: int, b: int) -> int:
    # `range(a, b, step)` with a constant nonzero step: the guard the
    # desugar emits is the same, and the checked arithmetic in the body
    # must stay in the source order the direct walk produces.
    total = 0
    for i in range(a, b, 2):
        total = total + i * 3
    return total


def count_down(a: int, b: int) -> int:
    # A negative step picks the descending arm of the desugar.
    total = 0
    for i in range(a, b, -1):
        total = total + i
    return total


def main() -> None:
    print(count_upto(5), count_between(1, 4), count_step(0, 6), count_down(4, 0))


if __name__ == "__main__":
    main()
