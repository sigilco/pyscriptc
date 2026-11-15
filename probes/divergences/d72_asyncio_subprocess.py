# SPDX-License-Identifier: Apache-2.0
# Divergence 72 (issue #1224): the async subprocess handle reads -1 for a
# running child's returncode, where CPython reads None (the closed subset
# has no optional int). CPython also spawns the child lazily through the
# loop's child watcher; here the fork/exec is the await itself. The probe
# reads returncode only after wait(), where both sides agree, and prints
# the codes, so both sides replay byte-identically.
import asyncio


async def main() -> None:
    proc: asyncio.subprocess.Process = await asyncio.create_subprocess_exec("sh", "-c", "exit 7")
    code: int = await proc.wait()
    print("waited", code)
    print("returncode", proc.returncode)


asyncio.run(main())
