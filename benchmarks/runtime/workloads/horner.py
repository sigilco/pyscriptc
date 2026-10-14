"""Float multiply-add loop. scriptc's numeric-kernels also smooths a Float64Array."""


def horner(n: int) -> float:
    acc: float = 0.0
    i: int = 0
    while i < n:
        acc = acc * 0.5 + 1.0
        i = i + 1
    return acc
