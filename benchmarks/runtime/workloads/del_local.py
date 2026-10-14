"""A loop that binds an int, deletes it unread, and keeps counting."""


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    x: int = 0
    while i < n:
        x = i
        del x
        total = total + 1
        i = i + 1
    return total
