"""The loopback HTTP server the net receipt runs against.

CPython's `http.server` drives it (ThreadingHTTPServer and the single
threaded base class), because `http.server` is still a fence in the compiler
(PS2001: the import is outside the table). The compiled server row slots in
here when the module lowers: the same client drives both, and the runner
prints the compiled row as pending until then.

The handler answers GET with a fixed body and POST with an echo of the
request body. Logging is off; the port is chosen by the kernel (bind port 0)
and printed on stdout.
"""

from __future__ import annotations

import http.server
import threading

BODY = b"net-benchmark-ok\n"


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:  # noqa: N802 (http.server's name)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def do_POST(self) -> None:  # noqa: N802 (http.server's name)
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


class Server:
    """One listening server on an ephemeral 127.0.0.1 port, serve_forever in a thread."""

    def __init__(self, threaded: bool) -> None:
        base = http.server.ThreadingHTTPServer if threaded else http.server.HTTPServer
        self.httpd = base(("127.0.0.1", 0), Handler)
        self.port: int = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def __enter__(self) -> Server:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
