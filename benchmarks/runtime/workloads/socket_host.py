"""socket.AF_INET is baked. gethostname calls embedded CPython."""

import socket


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        if socket.AF_INET == 2:
            total = total + 1
        host: str = socket.gethostname()
        total = total + len(host)
        i = i + 1
    return total
