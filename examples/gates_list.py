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


def splice() -> int:
    xs: list[int] = [1, 2, 3]
    xs[0:1] = [9, 8]
    if len(xs) != 4 or xs[0] != 9 or xs[1] != 8 or xs[3] != 3:
        return 1
    empty: list[int] = []
    xs[1:3] = empty
    if len(xs) != 2 or xs[1] != 3:
        return 2
    xs[2:2] = [7, 7, 7]
    if len(xs) != 5 or xs[4] != 7:
        return 3
    xs[-1:] = [4]
    if len(xs) != 5 or xs[4] != 4:
        return 4
    xs[:] = [1]
    if len(xs) != 1 or xs[0] != 1:
        return 5
    xs[5:9] = [2, 3]
    if len(xs) != 3 or xs[2] != 3:
        return 6
    xs[:] = xs
    if len(xs) != 3 or xs[0] != 1 or xs[2] != 3:
        return 7
    return 0


def delidx() -> int:
    xs: list[int] = [1, 2, 3]
    del xs[1]
    if len(xs) != 2 or xs[1] != 3:
        return 1
    del xs[-1]
    if len(xs) != 1 or xs[0] != 1:
        return 2
    d: dict[str, int] = {"a": 1, "b": 2}
    del d["a"]
    if "a" in d:
        return 3
    try:
        del xs[5]
        return 4
    except IndexError:
        pass
    try:
        del d["z"]
        return 5
    except KeyError:
        pass
    return 0
