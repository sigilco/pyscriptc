def total(xs: list[int]) -> int:
    acc: int = 0
    for item in xs:
        acc = acc + item
    return acc


def baked() -> int:
    xs: list[int] = [1, 2, 3, 4]
    return total(xs)


def countdown() -> int:
    acc: int = 0
    for i in range(3, 0, -1):
        acc = acc + i
    return acc
