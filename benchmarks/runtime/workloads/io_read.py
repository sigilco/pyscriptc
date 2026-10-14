"""IO[str] is the text handle open() returns. The checksum is n times len('Ada')."""

from typing import IO


def slurp(handle: IO[str]) -> str:
    return handle.read()


def bench(n: int) -> int:
    path: str = "/tmp/ps009-io-read.txt"
    with open(path, "w") as handle:
        handle.write("Ada")
    total: int = 0
    i: int = 0
    while i < n:
        with open(path) as handle:
            total = total + len(slurp(handle))
        i = i + 1
    return total
