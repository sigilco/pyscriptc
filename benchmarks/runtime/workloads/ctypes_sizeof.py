"""ctypes.sizeof of c_int and c_void_p, folded at compile time."""

import ctypes


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + ctypes.sizeof(ctypes.c_int) + ctypes.sizeof(ctypes.c_void_p)
        i = i + 1
    return total
