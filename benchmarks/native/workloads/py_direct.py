"""A Python loop, then one call into libmix's C ABI."""

import nativeprobe


def python_heavy(n: int) -> int:
    acc: int = 0
    i: int = 0
    payload: bytes = b"the quick brown fox jumps over the lazy dog"
    while i < n:
        acc = acc + i * 3
        i = i + 1
    return acc + nativeprobe.crc32(payload)
