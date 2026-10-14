"""time.strftime of the UTC epoch. sleep(0) runs once, outside the checksum."""

import time


def bench(n: int) -> int:
    time.sleep(0)
    clock: time.struct_time = time.gmtime(0)
    total: int = 0
    i: int = 0
    while i < n:
        text: str = time.strftime("%Y-%m-%d", clock)
        total = total + len(text)
        i = i + 1
    return total
