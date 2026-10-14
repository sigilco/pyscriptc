"""shutil.copyfile of a short text file."""

import shutil


def bench(n: int) -> int:
    src: str = "/tmp/ps-shutil-bench-src.txt"
    dst: str = "/tmp/ps-shutil-bench-dst.txt"
    with open(src, "w") as handle:
        handle.write("hello")
    total: int = 0
    i: int = 0
    while i < n:
        got: str = shutil.copyfile(src, dst)
        total = total + len(got)
        i = i + 1
    return total
