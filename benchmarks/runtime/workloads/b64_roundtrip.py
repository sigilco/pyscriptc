"""base64 encode and decode."""

import base64


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        raw: bytes = base64.b64decode(base64.b64encode(b"hi-there"))
        total = total + len(raw)
        i = i + 1
    return total
