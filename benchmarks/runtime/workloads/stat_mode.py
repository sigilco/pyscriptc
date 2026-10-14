"""stat.S_ISREG on a file this process just wrote."""

import io
import os
import stat


def bench(n: int) -> int:
    path: str = "/tmp/pyscriptc-bench-stat.txt"
    handle: io.StringIO = open(path, "w")  # type: ignore[assignment]
    handle.write("hi")
    handle.close()
    total: int = 0
    i: int = 0
    while i < n:
        info: os.stat_result = os.stat(path)
        if stat.S_ISREG(info.st_mode):
            total = total + info.st_size
        i = i + 1
    return total
