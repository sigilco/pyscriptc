"""fnmatch.fnmatch and fnmatch.filter. Matching is case-sensitive on this host."""

import fnmatch


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        if fnmatch.fnmatch("Foo.txt", "*.txt"):
            total = total + 1
        if fnmatch.fnmatch("Foo.txt", "*.TXT"):
            total = total + 10
        matched: list[str] = fnmatch.filter(["a.txt", "b.dat"], "*.txt")
        total = total + len(matched[0])
        i = i + 1
    return total
