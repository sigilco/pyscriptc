"""A loop that matches an int against a literal and keeps counting."""


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        match i:
            case 0:
                total = total + 1
        total = total + 1
        i = i + 1
    return total
