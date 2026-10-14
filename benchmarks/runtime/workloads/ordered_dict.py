"""`len` of an empty OrderedDict, which is the empty dict."""

import collections


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + len(collections.OrderedDict())
        i = i + 1
    return total
