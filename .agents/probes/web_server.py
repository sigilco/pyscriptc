"""Web server archetype probe: the http.server closed subset.

The shape a beta tester writes for a JSON service, before the
`pyscriptc-fastapi` port lands: a `BaseHTTPRequestHandler` subclass and an
`HTTPServer`. This is the probe the apps strategy measures the web archetype
with; see `.agents/decisions.md` (the package tiering policy).

Expected today: 4 fences.
  PS2002 import-table    http.server is not in the closed import table
  PS2002 class-cascade   this class base is not lowered
  PS1106 unlowered-type-name  the annotation HTTPServer names a type that is not lowered
  PS2002 name-resolution server is not a local or parameter (a cascade)

The route to 0 fences is #227, #228, #229 and #230.
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body: bytes = json.dumps({"path": self.path, "ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length: int = int(self.headers.get("Content-Length", "0"))
        payload: bytes = self.rfile.read(length)
        body: bytes = json.dumps({"echo": len(payload)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


def serve(port: int) -> int:
    server: HTTPServer = HTTPServer(("127.0.0.1", port), Handler)
    server.serve_forever()
    return 0
