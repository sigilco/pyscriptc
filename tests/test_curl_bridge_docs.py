"""HTTP docs, documented and kept from rotting.

The user-facing page is docs/guide/http.md. This test compiles the page's
native-client example (a GET through urllib.request against a local
listener) and the curl escape-hatch example, and checks the round trips
the page promises.

The native path is `urlopen` over `ps_http.c`; the escape hatch is
`subprocess.check_output(["curl", ...])`, an island call that runs curl
from the embedded CPython. POSIX only for the escape hatch: ps_sub.c
forks, and the binary must stay libpython-linked for the island call to
exist.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from tests.test_gates import compile_run, write_case

PROGRAM = """import json
import subprocess


def fetch(url: str) -> str:
    out: bytes = subprocess.check_output(["curl", "-s", url])
    return out.decode("utf-8")


def status(body: str) -> str:
    doc: dict[str, str] = json.loads(body)
    return doc["status"]


def post(url: str, payload: str) -> str:
    out: bytes = subprocess.check_output(
        ["curl", "-s", "-X", "POST", "-H", "Content-Type: application/json", "-d", payload, url]
    )
    return out.decode("utf-8")
"""

NATIVE_PROGRAM = """import json
import urllib.request


def fetch(url: str) -> str:
    resp: urllib.request.HTTPResponse = urllib.request.urlopen(url)
    body: bytes = resp.read()
    return body.decode("utf-8")


def status(body: str) -> str:
    doc: dict[str, str] = json.loads(body)
    return doc["status"]
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture()
def local_json_server() -> str:
    """A local HTTP server answering GET and POST with a fixed JSON doc."""

    doc = json.dumps({"status": "ok", "echo": None}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(doc)))
            self.end_headers()
            self.wfile.write(doc)

        do_POST = do_GET

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", _free_port()), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.skipif(sys.platform == "win32", reason="ps_sub.c forks, POSIX only")
class TestCurlBridge:
    def test_get_and_json_round_trip(self, tmp_path: Path, local_json_server: str) -> None:
        source = write_case(tmp_path, "curl_bridge.py", PROGRAM)
        binary = tmp_path / "curl_bridge-fetch"
        got = compile_run(tmp_path, source, "fetch", repr([local_json_server]))
        assert got.returncode == 0, got.stderr
        assert got.stdout == json.dumps({"status": "ok", "echo": None}) + "\n"
        assert binary.exists() or (tmp_path / "fetch").exists()

    def test_post_round_trip(self, tmp_path: Path, local_json_server: str) -> None:
        source = write_case(tmp_path, "curl_bridge_post.py", PROGRAM)
        got = compile_run(tmp_path, source, "post", repr([local_json_server, '{"status":"sent"}']))
        assert got.returncode == 0, got.stderr
        assert got.stdout == json.dumps({"status": "ok", "echo": None}) + "\n"

    def test_status_json_loads_round_trip(self, tmp_path: Path, local_json_server: str) -> None:
        source = write_case(tmp_path, "curl_bridge_status.py", PROGRAM)
        got = compile_run(tmp_path, source, "status", repr([json.dumps({"status": "ok"})]))
        assert got.returncode == 0, got.stderr
        assert got.stdout == "ok\n"

    def test_doc_example_stays_in_sync(self) -> None:
        """The native GET example on the guide page is the program this test compiles."""

        page = Path(__file__).resolve().parents[1] / "docs" / "guide" / "http.md"
        text = page.read_text(encoding="utf-8")
        assert "import urllib.request" in text
        assert "resp: urllib.request.HTTPResponse = urllib.request.urlopen(url)" in text
        assert 'body: bytes = resp.read()' in text
        assert 'body.decode("utf-8")' in text
        assert 'doc["status"]' in text
        # The escape hatch stays on the page with its exact call shape.
        assert 'subprocess.check_output(["curl", "-s", url])' in text

    def test_native_get_round_trip(self, tmp_path: Path, local_json_server: str) -> None:
        source = write_case(tmp_path, "http_native.py", NATIVE_PROGRAM)
        got = compile_run(tmp_path, source, "fetch", repr([local_json_server]))
        assert got.returncode == 0, got.stderr
        assert got.stdout == json.dumps({"status": "ok", "echo": None}) + "\n"

    def test_native_status_json_loads_round_trip(self, tmp_path: Path, local_json_server: str) -> None:
        source = write_case(tmp_path, "http_native_status.py", NATIVE_PROGRAM)
        got = compile_run(tmp_path, source, "status", repr([json.dumps({"status": "ok"})]))
        assert got.returncode == 0, got.stderr
        assert got.stdout == "ok\n"
