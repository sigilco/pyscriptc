"""unicodedata category and combining. The hot path is a direct Latin-1 table."""

import unicodedata


def bench(n: int) -> int:
    sample: str = "Ae2( "
    total: int = 0
    i: int = 0
    k: int = 0
    while i < n:
        ch: str = sample[k]
        total = total + len(unicodedata.category(ch))
        total = total + unicodedata.combining(ch)
        i = i + 1
        k = k + 1
        if k == 5:
            k = 0
    return total
