"""posixpath join, normpath, and splitext. No filesystem."""

import posixpath


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        joined: str = posixpath.join("/a", "b", "c")
        normal: str = posixpath.normpath("/a/../b//c")
        ext: str = posixpath.splitext("a.tar.gz")[1]
        total = total + len(joined) + len(normal) + len(ext)
        i = i + 1
    return total
