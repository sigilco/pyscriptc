"""warnings.warn. The message goes to stderr; the checksum is the call count."""

import warnings


def bench(n: int) -> int:
    i: int = 0
    while i < n:
        warnings.warn("bench")  # noqa: B028
        i = i + 1
    return n
