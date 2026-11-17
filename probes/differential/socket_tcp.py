# SPDX-License-Identifier: Apache-2.0
"""The blocking TCP socket surface over the loopback (issue #1270).

The subset is `socket.socket(AF_INET, SOCK_STREAM)`, `bind`, `listen`,
`accept`, `connect`, `recv`, `send`, `sendall`, `close`, `setsockopt`
(SO_REUSEADDR) and the context-manager form. The probe drives a loopback
echo: the server binds port 0 (the kernel picks the port, so two runs
never collide), reads the assigned port back with `getsockname`, the
client connects to it, and the bytes that travel the connection are
compared on both sides.

Everything printed is a fact both sides agree on: the bound host, that
the bound port is a real port, the peer host of the accepted connection,
that the echoed payload came back byte for byte, that `sendall` sent
everything, and that `send` answers the byte count CPython answers. The
port numbers themselves differ between the runs, so the probe prints
booleans about them.

The lane byte-compares stdout, stderr and the exit code against the
pinned CPython.
"""

import socket

PORT: int = 0


def run() -> int:
    server: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", PORT))
    server.listen(1)

    bound = server.getsockname()
    print("bound host", bound[0] == "127.0.0.1")
    print("bound port", bound[1] > 0)

    client: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(("127.0.0.1", bound[1]))

    conn, peer = server.accept()
    print("peer host", peer[0] == "127.0.0.1")

    client.sendall(b"hello tcp")
    received: bytes = conn.recv(64)
    print("received", received == b"hello tcp")

    conn.sendall(b"echo ok")
    echoed: bytes = client.recv(64)
    print("echoed", echoed == b"echo ok")

    sent: int = client.send(b"short")
    print("sent", sent == 5)

    conn.close()
    client.close()
    server.close()
    return 0


run()
