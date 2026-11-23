# SPDX-License-Identifier: Apache-2.0
"""The http.server handler surface over the loopback (issue #1270).

The closed surface is `HTTPServer((host, port), Handler)` with a handler
class that extends `BaseHTTPRequestHandler` and defines `do_GET`, plus
`send_response`, `send_header`, `end_headers`, `wfile.write`,
`headers.get` and `self.path`. The probe binds the server on port 0 (the
kernel picks the port, so two runs never collide), reads the assigned
port back through `server_port`, and a client thread sends one request
over the loopback and checks what came back.

Everything printed is a fact both sides agree on: that the assigned port
is a real port, that the request line's target reached the handler as
`self.path`, that the header the client sent came back through
`headers.get`, that the status line is the one the handler sent, and that
the body came back byte for byte. The port numbers themselves differ
between the runs, so the probe prints a boolean about the port.

The lane byte-compares stdout, stderr and the exit code against the
pinned CPython.
"""

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("X-Echo", self.headers.get("X-Probe"))
        self.end_headers()
        body: bytes = ("hello " + self.path).encode()
        self.wfile.write(body)


def client(port: int) -> None:
    conn: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    conn.connect(("127.0.0.1", port))
    conn.sendall(b"GET /probe?q=1 HTTP/1.0\r\nHost: probe\r\nX-Probe: sent\r\n\r\n")
    data: bytes = b""
    while True:
        chunk: bytes = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    print("status", data.startswith(b"HTTP/1.0 200 OK"))
    print("path", data.endswith(b"hello /probe?q=1"))
    print("header", b"X-Echo: sent" in data)
    conn.close()


def run() -> int:
    srv: HTTPServer = HTTPServer(("127.0.0.1", 0), Handler)
    print("bound port", srv.server_port > 0)

    worker: threading.Thread = threading.Thread(target=client, args=(srv.server_port,))
    worker.start()
    srv.handle_request()
    worker.join()
    srv.server_close()
    return 0


run()
