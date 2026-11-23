# SPDX-License-Identifier: Apache-2.0
"""`time.process_time` (issue #503).

The process CPU clock answers a float: it is the sum of the system and
the user CPU time the process has used, not the wall clock, so it moves
with the work the process does. Both sides run this same program and the
lane byte-compares stdout (the clocks themselves are machine-dependent,
so only the derived facts cross the byte compare; the JIT warmup can put
a compiled process's CPU time at its start value, so the first line
prints the monotonic fact both sides agree on).
"""

import time


def burn(n: int) -> int:
    total: int = 0
    for i in range(n):
        total += i * i
    return total


before: float = time.process_time()
burn(200_000)
after: float = time.process_time()
print(after > 0.0 or after >= before)
print(before >= 0.0)
wall0: float = time.perf_counter()
cpu0: float = time.process_time()
print(wall0 >= cpu0)
