"""urllib.parse quote, urlsplit, and parse_qs."""

import urllib.parse


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        quoted: str = urllib.parse.quote("a b/c")
        path: str = urllib.parse.urlsplit("http://h/p?q=1#f").path
        query: dict[str, list[str]] = urllib.parse.parse_qs("a=1&b=2")
        total = total + len(quoted) + len(path) + len(query["a"][0])
        i = i + 1
    return total
