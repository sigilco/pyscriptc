"""io.StringIO and io.BytesIO. Builtin open is the os-files workload."""

import io


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        mem: io.StringIO = io.StringIO("ab")
        total = total + len(mem.read())
        empty: io.StringIO = io.StringIO("")
        total = total + empty.write("z")
        raw: io.BytesIO = io.BytesIO(b"ab")
        total = total + len(raw.read())
        i = i + 1
    return total
