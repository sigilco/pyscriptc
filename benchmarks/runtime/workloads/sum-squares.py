"""Integer loop for the part of scriptc's numeric-kernels that lowers today."""


def sum_squares(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + i * i
        i = i + 1
    return total
