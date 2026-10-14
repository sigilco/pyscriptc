"""hmac.digest of one token. The hash runs in the small runtime, not on the island."""

import hmac


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        digest: bytes = hmac.digest(b"key", b"msg", "sha256")
        total = total + len(digest) + digest[0]
        i = i + 1
    return total
