# SPDX-License-Identifier: Apache-2.0
"""The asyncio stream surface over the loopback (issue #1281).

The subset is `asyncio.open_connection(host, port)` and the
`StreamReader` / `StreamWriter` pair: the reader's `read`, `readline` and
`readexactly`, the writer's `write`, `drain`, `close` and `wait_closed`.
The probe drives a loopback echo: a `threading.Thread` runs the blocking
`socket` server (bind port 0 so two runs never collide, the assigned port
read back through `getsockname` and handed over through a
`threading.Event` so the client connects to the port the kernel picked),
the client task connects with `open_connection`, writes the payloads,
and reads the echoes back through all three reader calls.

Everything printed is a fact both sides agree on: the round trips byte
for byte, `read(n)`'s short read (the bytes the peer had when it
answered), `readline`'s trailing newline, `readexactly`'s exact count,
and the writer's buffered write riding one drain.

The lane byte-compares stdout, stderr and the exit code against the
pinned CPython.
"""

import asyncio
import socket
import threading

BOUND = 0
ASSIGNED = threading.Event()


def server() -> None:
    global BOUND
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    BOUND = srv.getsockname()[1]
    ASSIGNED.set()
    conn, peer = srv.accept()
    # The echo server: every byte the client sends comes back. The
    # connection closes when the client closes its side.
    while True:
        got = conn.recv(1024)
        if got == b"":
            break
        conn.sendall(got)
    conn.close()
    srv.close()


async def client(port: int) -> int:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    # readline: the echo comes back as one line, newline in.
    writer.write(b"line one\n")
    await writer.drain()
    line = await reader.readline()
    print("readline", line)
    # read(n): a short read answers the bytes the peer had.
    writer.write(b"abcdef")
    await writer.drain()
    part = await reader.read(4)
    print("read4", part)
    rest = await reader.read(4)
    print("read-rest", rest)
    # readexactly: exactly the bytes asked for.
    writer.write(b"0123456789")
    await writer.drain()
    exact = await reader.readexactly(10)
    print("exactly", exact)
    writer.close()
    await writer.wait_closed()
    return 0


t = threading.Thread(target=server, args=())
t.start()
ASSIGNED.wait()
code = asyncio.run(client(BOUND))
t.join()
print("exit", code)
