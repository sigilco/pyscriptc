from __future__ import annotations
import asyncio
async def run() -> str:
    proc: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        "echo","hi", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out: bytes
    err: bytes
    out, err = await proc.communicate()
    return out.decode().strip()
async def main() -> None:
    print(await run())
if __name__ == "__main__": asyncio.run(main())
