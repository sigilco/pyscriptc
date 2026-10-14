"""The loop lives in libmix.so. Python makes one call."""

import nativeprobe


def so_heavy(n: int) -> int:
    payload: bytes = b"the quick brown fox jumps over the lazy dog"
    return nativeprobe.crc32_n(payload, n)
