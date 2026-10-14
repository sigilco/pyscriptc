"""struct.pack and struct.unpack of two little-endian ints."""

import struct


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        packed: bytes = struct.pack("<ii", i, 2)
        total = total + struct.unpack("<ii", packed)[0]
        i = i + 1
    return total
