"""x is None is == against None. The checksum counts a str that is not None and a None that is."""


def bench(n: int) -> int:
    text: str = "Ada"
    total: int = 0
    i: int = 0
    while i < n:
        hit: int = 0
        if text is not None:
            hit = 1
        miss: int = 0
        if text is None:
            miss = 1
        empty: None = None
        blank: int = 0
        if empty is None:
            blank = 1
        total = total + hit + miss + blank
        i = i + 1
    return total
