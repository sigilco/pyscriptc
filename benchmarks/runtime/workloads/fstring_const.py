"""A module f-string of str constants is one str. The checksum is n times its length."""

__author__ = "Ada"
__copyright__ = f"2014 {__author__}"


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + len(__copyright__)
        i = i + 1
    return total
