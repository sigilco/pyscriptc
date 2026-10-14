"""tempfile.gettempdir and gettempprefix."""

import tempfile


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + len(tempfile.gettempdir()) + len(tempfile.gettempprefix())
        i = i + 1
    return total
