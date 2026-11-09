def squares(n: int):
    i = 0
    while i < n:
        yield i * i
        i = i + 1


def main() -> int:
    it = squares(4)
    total = 0
    for v in it:
        total = total + v
    return total


print(main())
