import nativeprobe


def digest() -> int:
    return nativeprobe.crc32(b"the quick brown fox")
