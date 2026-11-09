"""The web-framework examples compile and serve.

The two examples under `examples/web/` are the first shippable web story:
a Flask app and a FastAPI app, each compiled into one binary with the
framework on the island floor. This test is the smoke test for that story.

It is skipped when the framework is not installed, because the whole point
of the example is that the framework is a real dependency: the compile
needs it importable (the type-checker and the island seed read it) and the
run needs it importable (the island floor executes it).

The compile needs `--skip-check`: the checker cannot see the framework
unless the framework's own site-packages is on the checker's path, and the
island floor does not need the checker's verdict.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "web"

ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def _candidate_sites() -> list[str]:
    """Every site-packages directory the interpreter can see.

    The project venv first, then the `PYTHONPATH` entries, then the
    interpreter's own `purelib` and `platlib`.
    """
    candidates: list[str] = []
    for base in (ROOT / ".venv" / "lib", ROOT / "venv" / "lib"):
        for entry in sorted(Path(base).glob("python*/site-packages")):
            if entry.is_dir():
                candidates.append(str(entry))
    for name in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if name and Path(name).is_dir():
            candidates.append(name)
    for key in ("purelib", "platlib"):
        path = sysconfig.get_paths().get(key)
        if path and Path(path).is_dir():
            candidates.append(path)
    return candidates


def _site_with(module: str) -> str | None:
    """The first candidate site-packages the module imports from."""
    for candidate in _candidate_sites():
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([candidate, str(ROOT / "src")])}
        probe = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            env=env,
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return candidate
    return None


def _has_framework(module: str) -> bool:
    """Is the framework importable from any candidate site-packages?"""
    return _site_with(module) is not None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get(port: int, path: str, timeout: float = 30.0) -> tuple[int, str]:
    """Wait for the server to answer, then return the status and the body."""
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
                sock.sendall(b"GET %s HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n" % path.encode())
                data = b""
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    data += chunk
            head, _, body = data.partition(b"\r\n\r\n")
            status = int(head.split(b" ")[1])
            return status, body.decode("utf-8", errors="replace")
        except OSError as exc:
            last = str(exc)
            time.sleep(0.05)
    raise AssertionError(f"the server never answered on port {port}: {last}")


def _compile(tmp_path: Path, name: str, port: int, env: dict[str, str]) -> Path:
    """Compile the example with the framework on the path.

    The example reads its port from the command line so the test can run it
    without colliding with anything else on the machine.
    """
    source = EXAMPLES / name
    body = source.read_text(encoding="utf-8").replace("port=8080", f"port={port}")
    staged = tmp_path / name
    staged.write_text(body, encoding="utf-8")
    got = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(staged),
            "--island",
            "--embed-python",
            "host",
            "--emit",
            "bin",
            "--skip-check",
            "-o",
            str(tmp_path / "app"),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="latin-1",
    )
    assert got.returncode == 0, f"the compile failed:\n{got.stdout}\n{got.stderr}"
    return tmp_path / "app"


def _serve(binary: Path, env: dict[str, str]) -> subprocess.Popen[bytes]:
    proc = subprocess.Popen(
        [str(binary)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc


@pytest.mark.skipif(not _has_framework("flask"), reason="flask is not installed")
def test_flask_example_compiles_and_serves(tmp_path: Path) -> None:
    site = _site_with("flask")
    assert site is not None
    env = {**ENV, "PYTHONPATH": os.pathsep.join([site, str(ROOT / "src")])}
    port = _free_port()
    binary = _compile(tmp_path, "flask_app.py", port, env)
    assert binary.is_file()

    proc = _serve(binary, env)
    try:
        status, body = _get(port, "/")
        assert status == 200
        assert "hello from flask" in body

        status, body = _get(port, "/health")
        assert status == 200
        assert "ok" in body
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.skipif(not _has_framework("fastapi"), reason="fastapi is not installed")
def test_fastapi_example_compiles_and_serves(tmp_path: Path) -> None:
    site = _site_with("fastapi")
    assert site is not None
    env = {**ENV, "PYTHONPATH": os.pathsep.join([site, str(ROOT / "src")])}
    port = _free_port()
    binary = _compile(tmp_path, "fastapi_app.py", port, env)
    assert binary.is_file()

    proc = _serve(binary, env)
    try:
        status, body = _get(port, "/")
        assert status == 200
        assert "from fastapi" in body

        status, body = _get(port, "/health")
        assert status == 200
        assert "ok" in body
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_examples_declare_the_island_floor() -> None:
    """Both examples say how to build them, in the file a reader opens."""
    for name in ("flask_app.py", "fastapi_app.py"):
        text = (EXAMPLES / name).read_text(encoding="utf-8")
        assert "--island" in text, f"{name} does not name the island flag"
        assert "pyscriptc compile" in text, f"{name} does not carry the build command"


def test_guide_page_states_the_gaps() -> None:
    """The guide page names the divergences instead of listing features."""
    page = ROOT / "docs" / "guide" / "web-frameworks.md"
    text = page.read_text(encoding="utf-8")
    assert "PS4302" in text, "the page must state the bundle refusal"
    assert "divergence 29" in text, "the page must point at the inbound-socket gap"
    for label in ("Flask", "FastAPI"):
        assert label in text, f"the page must cover {label}"
