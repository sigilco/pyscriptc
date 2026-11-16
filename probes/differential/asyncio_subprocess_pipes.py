# SPDX-License-Identifier: Apache-2.0
"""`asyncio.create_subprocess_exec` pipes the streams the caller asked for.

`stdout=`/`stderr=` take `asyncio.subprocess.PIPE`: the spawn pipes that
stream and `await proc.communicate()` hands the bytes back as the
(stdout, stderr) pair. The probe runs the same program on both sides and
the lane byte-compares stdout, stderr and the exit code.

The shapes here are the ones a caller writes: both streams piped, one
stream piped with a child that exits nonzero, the plain form where the
child inherits and the wait path reads the exit code, and two children
parked at once with one of them piped.
"""

import asyncio


async def main() -> None:
    # Both streams piped: the bytes come back as a pair.
    piped: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        "sh",
        "-c",
        "echo out-line; echo err-line >&2",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out: bytes
    err: bytes
    out, err = await piped.communicate()
    print("out", out.decode().strip())
    print("err", err.decode().strip())
    print("rc", piped.returncode)

    # One stream piped and a child that exits nonzero: the pipe drains and
    # the exit code rides the same record the wait path reads.
    one: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        "sh", "-c", "echo before; exit 3", stdout=asyncio.subprocess.PIPE
    )
    before: bytes
    _rest: bytes
    before, _rest = await one.communicate()
    print("before", before.decode().strip())
    print("rc3", one.returncode)

    # The plain form: the child inherits, and the wait path reads the code.
    plain: asyncio.subprocess.Process = await asyncio.create_subprocess_exec("sh", "-c", "exit 5")
    print("rc5", await plain.wait())

    # Two children parked at once, one of them piped: the loop wakes each
    # task as its child exits and the pipe drains at the communicate.
    slow: asyncio.subprocess.Process = await asyncio.create_subprocess_exec("sh", "-c", "sleep 0.2; exit 1")
    fast: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        "sh", "-c", "sleep 0.1; echo fast-out", stdout=asyncio.subprocess.PIPE
    )
    fast_out: bytes
    fast_err: bytes
    fast_out, fast_err = await fast.communicate()
    print("fast", fast_out.decode().strip())
    print("slow", await slow.wait())


asyncio.run(main())
