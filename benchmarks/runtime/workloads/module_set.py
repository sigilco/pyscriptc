"""A module set of str constants. The checksum is n times (len + one hit + one miss)."""

ARCHS = {"x86_64", "aarch64", "ppc64", "ppc64le", "s390x", "loongarch64", "riscv64"}


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        hit: int = 0
        if "aarch64" in ARCHS:
            hit = 1
        miss: int = 0
        if "wasm" in ARCHS:
            miss = 1
        total = total + len(ARCHS) + hit + miss
        i = i + 1
    return total
