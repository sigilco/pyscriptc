"""hashlib.sha256 hex digest."""

import hashlib


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        digest: str = hashlib.sha256(b"abc").hexdigest()
        total = total + len(digest)
        i = i + 1
    return total
