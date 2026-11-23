# SPDX-License-Identifier: Apache-2.0
"""http.server: the closed handler surface (issue #1270, slice 2).

The subset is `HTTPServer((host, port), Handler)` with a handler class
that extends `BaseHTTPRequestHandler` and defines `do_GET`/`do_POST`,
plus `send_response`, `send_header`, `end_headers`, `wfile.write`,
`rfile.read(n)`, `headers.get`, `self.path`, `serve_forever`,
`handle_request`, `shutdown`, `server_close`, `server_port` and
`server_address` over the loopback. The compiled output must match CPython
byte for byte; the port numbers differ between the runs, so the programs
print facts about them.

The fences are the ones the closed subset draws: an unknown module
member, an unknown server method, a handler that does not extend the
base, a handler with no `do_GET`/`do_POST`, a non-tuple endpoint and a
computed endpoint all refuse with PS2001/PS1003/PS2002.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import compile_run, write_case

from pyscriptc.lower import lower_source

GET_ECHO = """\
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
    conn.sendall(b"GET /probe?q=1 HTTP/1.0\\r\\nHost: probe\\r\\nX-Probe: sent\\r\\n\\r\\n")
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
"""

POST_BODY = """\
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        length: int = int(self.headers.get("Content-Length"))
        data: bytes = self.rfile.read(length)
        self.send_response(201)
        self.end_headers()
        self.wfile.write(data)


def client(port: int) -> None:
    conn: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    conn.connect(("127.0.0.1", port))
    conn.sendall(b"POST /upload HTTP/1.0\\r\\nHost: probe\\r\\nContent-Length: 5\\r\\n\\r\\nhello")
    data: bytes = b""
    while True:
        chunk: bytes = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    print("created", data.startswith(b"HTTP/1.0 201"))
    print("body", data.endswith(b"hello"))
    conn.close()


def run() -> int:
    srv: HTTPServer = HTTPServer(("127.0.0.1", 0), Handler)
    worker: threading.Thread = threading.Thread(target=client, args=(srv.server_port,))
    worker.start()
    srv.handle_request()
    worker.join()
    srv.server_close()
    return 0
"""

SERVER_ADDRESS = """\
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        pass


def run() -> int:
    srv: HTTPServer = HTTPServer(("127.0.0.1", 0), Handler)
    pair = srv.server_address
    print("host", pair[0] == "127.0.0.1")
    print("port", pair[1] > 0)
    srv.server_close()
    return 0
"""


# CPython's HTTPServer.server_bind calls socket.getfqdn(host): a reverse-DNS
# lookup for the bind address. On a macOS CI runner whose resolver does not
# answer the loopback PTR query, that call blocks for the resolver's own
# timeout (tens of seconds), and the oracle subprocess then outlives a
# 30-second budget while the program itself is fine. The subset's own
# runtime binds the literal and never asks the resolver, so only the
# CPython side pays.
#
# The oracle runner stubs the lookup: `server_name` (the answer getfqdn
# gives) is outside the compared surface, no program under probes/differential
# or in this file reads it, and the stub makes the oracle's bind as
# resolver-free as the subset's own. The budget is generous on purpose too:
# the programs answer in well under a second where the resolver answers, and
# a slow resolver must not read as a failure.
ORACLE_TIMEOUT = 180

_ORACLE_STUB = "import socket\nsocket.getfqdn = lambda name='': name or 'localhost'\n"


def _oracle_bytes(tmp_path: Path, name: str, source: str) -> str:
    """The compiled output must equal the CPython oracle's output.

    The handler surface is native, so its binary links no libpython and
    the link audit stays strict. The programs drive their own loopback
    client from a thread, so both sides see the same bytes.
    """
    path = write_case(tmp_path, name, source)
    runner = (
        _ORACLE_STUB + "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('mod', r'{path}')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "print(mod.run())\n"
    )
    expected = subprocess.run(
        [sys.executable, "-c", runner],
        capture_output=True,
        text=True,
        encoding="latin-1",
        check=True,
        timeout=ORACLE_TIMEOUT,
    ).stdout
    got = compile_run(tmp_path, path, "run", extra=[])
    assert got.returncode == 0, got.stderr
    assert got.stdout == expected, (got.stdout, expected)
    return got.stdout


def _fence(source: str, code: str, message: str) -> None:
    mod = lower_source(source)
    fences = mod["fences"]
    assert fences, source
    assert fences[0]["code"] == code, fences
    assert message in fences[0]["message"], fences


def test_the_get_echo_matches_cpython(tmp_path: Path) -> None:
    out = _oracle_bytes(tmp_path, "get.py", GET_ECHO)
    assert out == "bound port True\nstatus True\npath True\nheader True\n0\n"


def test_the_post_body_matches_cpython(tmp_path: Path) -> None:
    out = _oracle_bytes(tmp_path, "post.py", POST_BODY)
    assert out == "created True\nbody True\n0\n"


def test_server_address_matches_cpython(tmp_path: Path) -> None:
    out = _oracle_bytes(tmp_path, "addr.py", SERVER_ADDRESS)
    assert out == "host True\nport True\n0\n"


def test_the_committed_probe_matches_cpython(tmp_path: Path) -> None:
    """The differential probe byte-compares stdout, stderr and the exit code."""
    probe = Path(__file__).resolve().parent.parent / "probes" / "differential" / "http_server_loopback.py"
    assert probe.is_file()
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(probe),
            "--emit",
            "bin",
            "-o",
            str(tmp_path / "probe"),
            "--run",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env={**__import__("os").environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
    )
    assert built.returncode == 0, built.stderr
    oracle = subprocess.run(
        [sys.executable, "-c", _ORACLE_STUB + f"exec(open({str(probe)!r}).read())"],
        capture_output=True,
        text=True,
        timeout=ORACLE_TIMEOUT,
    )
    assert oracle.returncode == built.returncode
    assert oracle.stdout == built.stdout, (oracle.stdout, built.stdout)
    assert oracle.stderr == built.stderr


def test_the_unknown_module_member_fences() -> None:
    _fence(
        "from http.server import HTTPServer\n\nx = HTTPServer.test_only()\n",
        "PS2001",
        "HTTPServer.test_only is not in the lowered subset of http.server",
    )


def test_the_unknown_server_method_fences() -> None:
    _fence(
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self) -> None:\n"
        "        pass\n\n"
        "def run() -> int:\n"
        "    srv: HTTPServer = HTTPServer(('127.0.0.1', 0), Handler)\n"
        "    srv.fileno()\n"
        "    return 0\n",
        "PS2001",
        "HTTPServer.fileno is not in the lowered subset of http.server",
    )


def test_the_non_handler_class_fences() -> None:
    _fence(
        "from http.server import HTTPServer\n\n"
        "class Handler:\n"
        "    def do_GET(self) -> None:\n"
        "        pass\n\n"
        "srv: HTTPServer = HTTPServer(('127.0.0.1', 0), Handler)\n",
        "PS1003",
        "handler is a handler class",
    )


def test_the_handler_without_a_do_method_fences() -> None:
    _fence(
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    pass\n\n"
        "srv: HTTPServer = HTTPServer(('127.0.0.1', 0), Handler)\n",
        "PS2002",
        "defines no do_GET or do_POST",
    )


def test_the_non_tuple_endpoint_fences() -> None:
    _fence(
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self) -> None:\n"
        "        pass\n\n"
        "srv: HTTPServer = HTTPServer('127.0.0.1', Handler)\n",
        "PS1003",
        "takes a (host, port) tuple",
    )


def test_the_computed_host_fences() -> None:
    _fence(
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self) -> None:\n"
        "        pass\n\n"
        "def run() -> int:\n"
        "    host: int = 5\n"
        "    srv: HTTPServer = HTTPServer((host, 0), Handler)\n"
        "    return 0\n",
        "PS1003",
        "host is str",
    )
