"""inspect.cleandoc through the embedded interpreter."""

import inspect


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        doc: str = inspect.cleandoc("  hi\n  there")
        total = total + len(doc)
        i = i + 1
    return total
