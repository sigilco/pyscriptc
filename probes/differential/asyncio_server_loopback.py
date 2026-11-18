# SPDX-License-Identifier: Apache-2.0
"""The asyncio.start_server surface over the loopback (issue #1281, slice 2).

The subset is `asyncio.start_server(handler, host, port)` with the
`Server` handle: `serve_forever`, `close`, `is_serving` and `sockets`,
where `sockets` rides the socket surface's `getsockname` so a program
that binds port 0 reads the assigned port back. The handler is a compiled
coroutine over the stream pair (the reader's `read`, the writer's
`write`, `drain` and `close`), so the loopback echo rides the slice-1
runtime end to end.

The probe binds port 0 (the kernel picks the port, so two runs never
collide), starts the client task first and only then enters
`serve_forever`, so the accept and the connect race the same loop the
way CPython's do. The client connects over the loopback, writes one
payload, reads the echo back, closes, and closes the server; CPython's
`Server.close` cancels the `serve_forever` future, so the await raises
`CancelledError` there and the compiled serve loop raises the same
exception at its park. The probe catches it and prints the fact.

Everything printed is a fact both sides agree on: that the assigned port
is a real port, that the server answers `is_serving` before and after
the close, that the echo came back byte for byte, that the serve await
cancelled, and that the client task finished. The port numbers themselves
differ between the runs, so the probe prints a boolean about the port.

The lane byte-compares stdout, stderr and the exit code against the
pinned CPython.
"""

import asyncio
import socket


async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    data: bytes = await reader.read(100)
    writer.write(b"echo:" + data)
    await writer.drain()
    writer.close()


async def client(port: int, server: asyncio.Server) -> int:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"ping")
    await writer.drain()
    got: bytes = await reader.read(100)
    print("got", got)
    writer.close()
    server.close()
    return 0


async def main() -> int:
    server: asyncio.Server = await asyncio.start_server(handler, "127.0.0.1", 0)
    socks: tuple[socket.socket, ...] = server.sockets
    print("socks", len(socks) == 1)
    addr: tuple[str, int] = socks[0].getsockname()
    port: int = addr[1]
    print("port", port > 0)
    print("serving", server.is_serving())
    worker = asyncio.create_task(client(port, server))
    try:
        await server.serve_forever()
    except asyncio.CancelledError:
        print("serve cancelled")
    print("serving after close", server.is_serving())
    code: int = await worker
    print("client exit", code)
    return 0


print("exit", asyncio.run(main()))
