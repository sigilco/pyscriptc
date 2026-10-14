"""a if cond else b is an if that assigns the taken str. The checksum is its length."""


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    take: bool = True
    while i < n:
        word: str = "Ada" if take else "Lovelace"
        total = total + len(word)
        if take:
            take = False
        else:
            take = True
        i = i + 1
    return total
