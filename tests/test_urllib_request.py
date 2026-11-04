"""urllib.request: the native client over a loopback listener.

The tests bind a local HTTP listener (a thread in the test process) and
compile a small program against it, so no network is needed. The runtime
parity checks compare the compiled output against CPython 3.14 with the
same listener.
"""

from __future__ import annotations

import http.server
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from tests.test_gates import ROOT, compile_run, oracle, write_case


class _Handler(http.server.BaseHTTPRequestHandler):
    """Fixtures per path. The class attributes are set per test."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:  # noqa: D102
        return

    def _send(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._route()

    def do_HEAD(self) -> None:  # noqa: N802
        self._route()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)
        self._route(payload)

    def do_PUT(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)
        self._route(payload)

    def _route(self, payload: bytes = b"") -> None:
        path = self.path.split("?")[0]
        if path == "/plain":
            self._send(200, b"hello")
        elif path == "/echo":
            body = payload if payload else b""
            self._send(200, body)
        elif path == "/headers":
            rows = "".join(f"{key}: {value}\n" for key, value in sorted(self.headers.items()))
            self._send(200, rows.encode("utf-8"))
        elif path == "/chunked":
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for piece in (b"alpha", b"beta", b"gamma"):
                self.wfile.write(f"{len(piece):X}\r\n".encode("ascii"))
                self.wfile.write(piece + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        elif path == "/big":
            self._send(200, b"x" * 70000)
        elif path == "/notfound":
            self._send(404, b"missing")
        elif path == "/server-error":
            self._send(500, b"boom")
        elif path == "/redirect":
            self._send(302, b"", {"Location": "/plain"})
        elif path == "/redirect-relative":
            self._send(302, b"", {"Location": "plain"})
        elif path == "/redirect-303":
            self._send(303, b"", {"Location": "/echo"})
        elif path == "/redirect-307":
            self._send(307, b"", {"Location": "/echo"})
        elif path == "/redirect-308":
            self._send(308, b"", {"Location": "/echo"})
        elif path == "/redirect-loop":
            self._send(302, b"", {"Location": "/redirect-loop"})
        else:
            self._send(404, b"no route")


@pytest.fixture(scope="module")
def listener() -> str:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


def _case(tmp_path: Path, name: str, body: str) -> Path:
    return write_case(tmp_path, name, body)


def test_get_reads_the_body(tmp_path: Path, listener: str) -> None:
    path = _case(
        tmp_path,
        "get.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    body: bytes = r.read()",
                "    return body.decode('utf-8')",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr([f"{listener}/plain"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "hello\n"
    assert got.stdout == oracle(path, "f", repr([f"{listener}/plain"]))


def test_status_and_headers(tmp_path: Path, listener: str) -> None:
    path = _case(
        tmp_path,
        "status.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> int:",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    return r.status",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr([f"{listener}/plain"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "200\n"


def test_getcode_shim(tmp_path: Path, listener: str) -> None:
    path = _case(
        tmp_path,
        "getcode.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> int:",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    return r.getcode()",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr([f"{listener}/plain"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "200\n"


def test_chunked_body(tmp_path: Path, listener: str) -> None:
    path = _case(
        tmp_path,
        "chunked.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    body: bytes = r.read()",
                "    return body.decode('utf-8')",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr([f"{listener}/chunked"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "alphabetagamma\n"


def test_large_body(tmp_path: Path, listener: str) -> None:
    path = _case(
        tmp_path,
        "big.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> int:",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    body: bytes = r.read()",
                "    return len(body)",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr([f"{listener}/big"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "70000\n"


def test_post_body(tmp_path: Path, listener: str) -> None:
    path = _case(
        tmp_path,
        "post.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(url, b'hello')",
                "    body: bytes = r.read()",
                "    return body.decode('utf-8')",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr([f"{listener}/echo"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "hello\n"


def test_request_object_posts(tmp_path: Path, listener: str) -> None:
    path = _case(
        tmp_path,
        "request.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    req: urllib.request.Request = urllib.request.Request(url, data=b'hello')",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(req)",
                "    body: bytes = r.read()",
                "    return body.decode('utf-8')",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr([f"{listener}/echo"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "hello\n"


def test_request_method_put(tmp_path: Path, listener: str) -> None:
    path = _case(
        tmp_path,
        "put.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    req: urllib.request.Request = urllib.request.Request(url, data=b'x', method='PUT')",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(req)",
                "    body: bytes = r.read()",
                "    return body.decode('utf-8')",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr([f"{listener}/echo"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "x\n"


def test_error_status_raises(tmp_path: Path, listener: str) -> None:
    path = _case(
        tmp_path,
        "error.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> int:",
                "    caught: int = 0",
                "    try:",
                "        r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    except OSError:",
                "        caught = 1",
                "    return caught",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr([f"{listener}/notfound"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "1\n"


def test_redirect_is_followed(tmp_path: Path, listener: str) -> None:
    path = _case(
        tmp_path,
        "redirect.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    body: bytes = r.read()",
                "    return body.decode('utf-8')",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr([f"{listener}/redirect"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "hello\n"


def test_relative_redirect_is_followed(tmp_path: Path, listener: str) -> None:
    path = _case(
        tmp_path,
        "relative.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    body: bytes = r.read()",
                "    return body.decode('utf-8')",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr([f"{listener}/redirect-relative"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "hello\n"


def test_redirect_loop_raises(tmp_path: Path, listener: str) -> None:
    path = _case(
        tmp_path,
        "loop.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> int:",
                "    caught: int = 0",
                "    try:",
                "        r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    except OSError:",
                "        caught = 1",
                "    return caught",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr([f"{listener}/redirect-loop"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "1\n"


def test_info_shim_reads_headers(tmp_path: Path, listener: str) -> None:
    path = _case(
        tmp_path,
        "info.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> int:",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    info: str = r.info()",
                "    marked: int = 0",
                "    if 'Content-Length' in info:",
                "        marked = 1",
                "    return marked",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr([f"{listener}/headers"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "1\n"


def test_request_headers_reach_the_wire(tmp_path: Path, listener: str) -> None:
    path = _case(
        tmp_path,
        "headers.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> int:",
                "    req: urllib.request.Request = urllib.request.Request(",
                "        url, headers={'X-Marker': 'yes', 'Accept': 'text/plain'}",
                "    )",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(req)",
                "    body: bytes = r.read()",
                "    text: str = body.decode('utf-8')",
                "    marked: int = 0",
                "    if 'X-Marker' in text:",
                "        marked = 1",
                "    return marked",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr([f"{listener}/headers"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "1\n"


def test_timeout_raises(tmp_path: Path) -> None:
    path = _case(
        tmp_path,
        "timeout.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> int:",
                "    caught: int = 0",
                "    try:",
                "        r: urllib.request.HTTPResponse = urllib.request.urlopen(url, timeout=0.05)",
                "    except OSError:",
                "        caught = 1",
                "    return caught",
                "",
            ]
        ),
    )
    # A non-routable link-local address hangs without the timeout.
    got = compile_run(tmp_path, path, "f", repr(["http://10.255.255.1:81/plain"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "1\n"


def test_httperror_catches_error_status(tmp_path: Path, listener: str) -> None:
    path = _case(
        tmp_path,
        "httperror.py",
        "\n".join(
            [
                "import urllib.request",
                "import urllib.error",
                "",
                "def f(url: str) -> int:",
                "    code: int = 0",
                "    try:",
                "        r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "        code = r.status",
                "    except urllib.error.HTTPError:",
                "        code = 404",
                "    return code",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr([f"{listener}/notfound"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "404\n"


def test_httperror_catches_server_error(tmp_path: Path, listener: str) -> None:
    path = _case(
        tmp_path,
        "servererror.py",
        "\n".join(
            [
                "import urllib.request",
                "import urllib.error",
                "",
                "def f(url: str) -> int:",
                "    code: int = 0",
                "    try:",
                "        r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "        code = r.status",
                "    except urllib.error.HTTPError:",
                "        code = 500",
                "    return code",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr([f"{listener}/server-error"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "500\n"


def test_redirect_303_drops_the_body(tmp_path: Path, listener: str) -> None:
    path = _case(
        tmp_path,
        "redir303.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    req: urllib.request.Request = urllib.request.Request(url, data=b'payload')",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(req)",
                "    body: bytes = r.read()",
                "    return body.decode('utf-8')",
                "",
            ]
        ),
    )
    # 303 rewrites the POST to a GET, so the echo body arrives empty.
    got = compile_run(tmp_path, path, "f", repr([f"{listener}/redirect-303"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "\n"


def test_redirect_307_keeps_the_body(tmp_path: Path, listener: str) -> None:
    path = _case(
        tmp_path,
        "redir307.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    req: urllib.request.Request = urllib.request.Request(url, data=b'payload')",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(req)",
                "    body: bytes = r.read()",
                "    return body.decode('utf-8')",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr([f"{listener}/redirect-307"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "payload\n"


def test_redirect_308_keeps_the_body(tmp_path: Path, listener: str) -> None:
    path = _case(
        tmp_path,
        "redir308.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    req: urllib.request.Request = urllib.request.Request(url, data=b'payload')",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(req)",
                "    body: bytes = r.read()",
                "    return body.decode('utf-8')",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr([f"{listener}/redirect-308"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "payload\n"


def test_https_is_refused(tmp_path: Path) -> None:
    path = _case(
        tmp_path,
        "https.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> int:",
                "    caught: int = 0",
                "    try:",
                "        r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    except OSError:",
                "        caught = 1",
                "    return caught",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr(["https://example.com/"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "1\n"


# --- the https:// path over a registered TLS backend -------------------------
#
# The transport contract of ps_tls.h has one backend today: the plaintext
# TCP one. A TLS backend registers itself through ps_tls_backend(), and the
# https:// request then travels over that backend. The fake backend here is
# linked into the test binary through a manifest: it connects to the
# loopback listener like a TLS backend would connect to a TLS server, so a
# passing test proves the request went through the transport interface and
# not through the plaintext socket path.


FAKE_TLS = Path(__file__).resolve().parent / "fixtures" / "http_tls_backend"


def _compile_run_with_backend(tmp_path: Path, source: Path, func: str, args: str) -> subprocess.CompletedProcess[str]:
    """Compile and run `source` with the fake TLS backend linked in."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(source),
            "--ffi",
            str(FAKE_TLS / "fake_tls.toml"),
            "--run",
            "--call",
            func,
            "--args",
            args,
            "-o",
            str(tmp_path / func),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_https_goes_through_the_tls_backend(tmp_path: Path, listener: str) -> None:
    """With a backend registered, an https:// request reads the response."""
    path = _case(
        tmp_path,
        "https_backend.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    body: bytes = r.read()",
                "    return body.decode('utf-8')",
                "",
            ]
        ),
    )
    # The listener is a plaintext HTTP server; the fake backend speaks to it
    # over the transport, so the https:// URL resolves to the same host and
    # port as the http:// tests.
    url = listener.replace("http://", "https://")
    got = _compile_run_with_backend(tmp_path, path, "f", repr([f"{url}/plain"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "hello\n"


def test_https_backend_sees_the_request_headers(tmp_path: Path, listener: str) -> None:
    """The backend carries the request the HTTP layer wrote, headers included."""
    path = _case(
        tmp_path,
        "https_headers.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> str:",
                "    r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    body: bytes = r.read()",
                "    return body.decode('utf-8')",
                "",
            ]
        ),
    )
    url = listener.replace("http://", "https://")
    got = _compile_run_with_backend(tmp_path, path, "f", repr([f"{url}/headers"]))
    assert got.returncode == 0, got.stderr
    assert "Host:" in got.stdout


def test_https_without_a_backend_still_raises(tmp_path: Path) -> None:
    """With no backend linked, the https:// request raises URLError as before."""
    path = _case(
        tmp_path,
        "https_none.py",
        "\n".join(
            [
                "import urllib.request",
                "",
                "def f(url: str) -> int:",
                "    caught: int = 0",
                "    try:",
                "        r: urllib.request.HTTPResponse = urllib.request.urlopen(url)",
                "    except OSError:",
                "        caught = 1",
                "    return caught",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f", repr(["https://example.com/"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "1\n"
