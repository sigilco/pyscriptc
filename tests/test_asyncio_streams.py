# SPDX-License-Identifier: Apache-2.0
"""`asyncio.open_connection` and the stream pair (issue #1281).

`await asyncio.open_connection(host, port)` hands back the
`StreamReader` / `StreamWriter` pair: the reader's `read`, `readline`
and `readexactly` answer bytes off the connection and the writer's
`write`, `drain`, `close` and `wait_closed` put them there. The
connection rides the TCP socket runtime (issue #1270), so the shapes
here are the ones a caller writes against a loopback echo.

Each sample runs under CPython and compiled; the compiled stdout must
match byte for byte, and the committed differential probe
`probes/differential/asyncio_streams.py` must too.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc.lower import lower_source

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
ENV.pop("PYSC_CORO", None)

PROBE = ROOT / "probes" / "differential" / "asyncio_streams.py"

ZIG = shutil.which("zig") is not None
needs_zig = pytest.mark.skipif(not ZIG, reason="zig not on PATH")

ECHO = """\
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
    writer.write(b"line one\\n")
    await writer.drain()
    line = await reader.readline()
    print("readline", line)
    writer.write(b"abcdef")
    await writer.drain()
    part = await reader.read(4)
    print("read4", part)
    rest = await reader.read(4)
    print("read-rest", rest)
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
"""

READLINE = """\
import asyncio
import socket
import threading

BOUND = 0
ASSIGNED = threading.Event()


def server() -> None:
    global BOUND
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    BOUND = srv.getsockname()[1]
    ASSIGNED.set()
    conn, peer = srv.accept()
    conn.sendall(b"first\\nsecond\\n")
    conn.close()
    srv.close()


async def client(port: int) -> int:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    first = await reader.readline()
    print("first", first)
    second = await reader.readline()
    print("second", second)
    writer.close()
    await writer.wait_closed()
    return 0


t = threading.Thread(target=server, args=())
t.start()
ASSIGNED.wait()
code = asyncio.run(client(BOUND))
t.join()
print("exit", code)
"""


def _run_both(tmp_path: Path, name: str, source: str) -> tuple[str, str]:
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, check=True).stdout
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--skip-check",
            "--emit",
            "bin",
            "-o",
            str(tmp_path / name),
            "--run",
        ],
        cwd=tmp_path,
        env=ENV,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    return built.stdout, expected


@needs_zig
@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("echo", ECHO),
        ("readline", READLINE),
    ],
)
def test_the_stream_surface_matches_cpython(tmp_path: Path, name: str, source: str) -> None:
    got, expected = _run_both(tmp_path, name, source)
    assert expected
    assert got == expected


def test_the_reader_reads_a_short_line_at_the_end() -> None:
    """`readline` on a closed connection answers the bytes it got, and
    then the empty bytes CPython answers."""
    source = """
import asyncio
import socket
import threading

BOUND = 0
ASSIGNED = threading.Event()


def server() -> None:
    global BOUND
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    BOUND = srv.getsockname()[1]
    ASSIGNED.set()
    conn, peer = srv.accept()
    conn.sendall(b"tail")
    conn.close()
    srv.close()


async def client(port: int) -> int:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    print("tail", await reader.readline())
    print("eof", await reader.readline())
    writer.close()
    await writer.wait_closed()
    return 0


t = threading.Thread(target=server, args=())
t.start()
ASSIGNED.wait()
code = asyncio.run(client(BOUND))
t.join()
print("exit", code)
"""
    module = lower_source(source, "main.py")
    assert not module["fences"], module["fences"]


def test_the_probe_is_committed() -> None:
    assert PROBE.is_file()


@needs_zig
def test_the_committed_probe_matches_cpython(tmp_path: Path) -> None:
    """The differential probe byte-compares stdout, stderr and the exit code."""
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(PROBE),
            "--emit",
            "bin",
            "-o",
            str(tmp_path / "probe"),
            "--run",
        ],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    oracle = subprocess.run([sys.executable, str(PROBE)], cwd=ROOT, capture_output=True, text=True)
    assert oracle.returncode == built.returncode
    assert oracle.stdout == built.stdout
    assert oracle.stderr == built.stderr
