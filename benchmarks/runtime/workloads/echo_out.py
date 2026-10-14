"""subprocess.check_output of /bin/echo. Each step is a process."""

import subprocess


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        out: bytes = subprocess.check_output(["/bin/echo", "hi"])
        total = total + len(out)
        i = i + 1
    return total
