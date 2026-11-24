# SPDX-License-Identifier: Apache-2.0
"""`signal.signal` installs a native handler that runs at a safepoint.

The handler is a compiled function; the runtime installs a C-level
`sigaction` handler that only records the signal number, and the compiled
handler runs at the next safepoint, outside the signal context. That is
CPython's deferred-delivery model in shape: CPython's C handler sets a flag
too, and the Python-level handler runs from the eval loop.

The probe sends itself a signal and checks that the handler ran and the
program exited cleanly on both lanes. The handler takes the signal number
and, with a default, the frame CPython passes: a native binary has no frame,
so the second parameter keeps its default there.

The order of the output is the deferred delivery: the handler runs at the
safepoint after the `os.kill`, so the "caught" line comes after the kill
and before the next statement.
"""

import os
import signal

caught: list[int] = [0]


def handler(signum: int, frame: int = 0) -> None:
    caught[0] = signum
    print("caught", signum)


def main() -> None:
    signal.signal(signal.SIGTERM, handler)
    print("installed")
    os.kill(os.getpid(), signal.SIGTERM)
    print("after-kill")
    print("caught", caught[0])


main()
