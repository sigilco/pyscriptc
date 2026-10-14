"""The loop lives in libmix.so. One island call reaches it."""

import nativeprobe


def so_heavy(n: int) -> int:
    payload: bytes = b"the quick brown fox jumps over the lazy dog"
    return nativeprobe.crc32_n_island(payload, n)
