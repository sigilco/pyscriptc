def swap(a: int, b: int) -> int:
    a, b = b, a
    return a + b


def make(a: int, b: int):
    return a, b


def read(pair: tuple[int, int]) -> int:
    return pair[1]


def main() -> None:
    print(swap(3, 4))
    print(read(make(7, 9)))
