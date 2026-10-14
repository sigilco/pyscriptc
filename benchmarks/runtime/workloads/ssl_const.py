"""Baked ssl.OPENSSL_VERSION and ssl.HAS_SNI."""

import ssl


def bench(n: int) -> int:
    text: str = ssl.OPENSSL_VERSION
    total: int = 0
    i: int = 0
    while i < n:
        total = total + len(text)
        if ssl.HAS_SNI:
            total = total + 1
        i = i + 1
    return total
