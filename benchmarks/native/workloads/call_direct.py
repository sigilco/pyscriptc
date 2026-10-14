"""One C-ABI call per iteration. The .so does a little work each time."""

import nativeprobe


def call_heavy(n: int) -> int:
    acc: int = 0
    i: int = 0
    payload: bytes = b"the quick brown fox jumps over the lazy dog"
    while i < n:
        acc = acc + nativeprobe.crc32(payload)
        i = i + 1
    return acc
