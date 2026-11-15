# SPDX-License-Identifier: Apache-2.0
"""`asyncio.create_subprocess_exec` starts a child the coroutine awaits.

The call spawns the child, `await proc.wait()` parks the task until the
child exits and reads its exit code, and `proc.returncode` reads the same
code afterwards. The probe runs the same program on both sides and the
lane byte-compares stdout, stderr and the exit code.

The shapes here are the ones a caller writes: a child that fails, a child
that exits with a code of its own, the code read back off the handle, and
two children parked at once (the loop wakes them as each exits). The
children are `sh` one-liners, so the probe runs the same way on every
platform the lane covers.
"""

import asyncio


async def main() -> None:
    # A child that fails: the code is the shell's convention (1).
    failed: asyncio.subprocess.Process = await asyncio.create_subprocess_exec("false")
    print("failed", await failed.wait())

    # A child that exits with a code of its own.
    coded: asyncio.subprocess.Process = await asyncio.create_subprocess_exec("sh", "-c", "exit 7")
    print("coded", await coded.wait())

    # The code reads back off the handle after the wait.
    print("returncode", coded.returncode)

    # Two children parked at once: the loop wakes each task as its child
    # exits, so the waits finish in the order the children do.
    slow: asyncio.subprocess.Process = await asyncio.create_subprocess_exec("sh", "-c", "sleep 0.2; exit 3")
    fast: asyncio.subprocess.Process = await asyncio.create_subprocess_exec("sh", "-c", "sleep 0.1; exit 5")
    print("fast", await fast.wait())
    print("slow", await slow.wait())


asyncio.run(main())
