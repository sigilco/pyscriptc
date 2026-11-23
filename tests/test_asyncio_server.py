# SPDX-License-Identifier: Apache-2.0
"""`asyncio.start_server` and the `Server` surface (issue #1281, slice 2).

`await asyncio.start_server(handler, host, port)` binds the listening
socket and answers CPython's `Server` handle; `serve_forever()` takes
peers until `close()` cancels it the way CPython's future cancels, and
`is_serving` / `sockets` report the state and the bound socket. The
handler is a compiled coroutine over the stream pair (the slice-1
surface), so the loopback echo rides one runtime end to end.

Each sample runs under CPython and compiled; the compiled stdout must
match byte for byte, and the committed differential probe
`probes/differential/asyncio_server_loopback.py` must too.

The fences are the ones the closed subset draws: the keyword arguments
(`ssl`, `backlog`, the rest) refuse with PS2003, an unknown `Server`
method or module member refuses with PS2001, and a wrong callback shape
refuses with PS1003/PS2003.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
ENV.pop("PYSC_CORO", None)

PROBE = ROOT / "probes" / "differential" / "asyncio_server_loopback.py"

ZIG = shutil.which("zig") is not None
needs_zig = pytest.mark.skipif(not ZIG, reason="zig not on PATH")

ECHO = """\
import asyncio
import socket


async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    data: bytes = await reader.read(100)
    writer.write(b"echo:" + data)
    await writer.drain()
    writer.close()


async def client(port: int, server: asyncio.Server) -> int:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"ping")
    await writer.drain()
    got: bytes = await reader.read(100)
    print("got", got)
    writer.close()
    server.close()
    return 0


async def main() -> int:
    server: asyncio.Server = await asyncio.start_server(handler, "127.0.0.1", 0)
    socks: tuple[socket.socket, ...] = server.sockets
    print("socks", len(socks) == 1)
    addr: tuple[str, int] = socks[0].getsockname()
    port: int = addr[1]
    print("port", port > 0)
    worker = asyncio.create_task(client(port, server))
    try:
        await server.serve_forever()
    except asyncio.CancelledError:
        print("serve cancelled")
    print("serving", server.is_serving())
    code: int = await worker
    print("client exit", code)
    return 0


print("exit", asyncio.run(main()))
"""


def _fence(tmp_path: Path, name: str, source: str) -> str:
    """The compiler's stderr for a program that must not compile."""
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(path), "-o", str(tmp_path / name)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=ENV,
    )
    assert proc.returncode != 0, proc.stdout
    return proc.stderr


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


@needs_zig
def test_the_loopback_echo_matches_cpython(tmp_path: Path) -> None:
    """The loopback echo over `start_server` matches CPython byte for byte."""
    path = tmp_path / "echo.py"
    path.write_text(ECHO, encoding="utf-8")
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--emit",
            "bin",
            "-o",
            str(tmp_path / "echo"),
            "--run",
        ],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    oracle = subprocess.run([sys.executable, str(path)], cwd=ROOT, capture_output=True, text=True)
    assert oracle.returncode == built.returncode
    assert oracle.stdout == built.stdout
    assert oracle.stderr == built.stderr


def test_the_keyword_arguments_fence(tmp_path: Path) -> None:
    """The three-argument form is the closed surface: any keyword refuses."""
    stderr = _fence(
        tmp_path,
        "kw_backlog",
        """
import asyncio


async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    pass


async def main() -> int:
    server: asyncio.Server = await asyncio.start_server(handler, "127.0.0.1", 0, backlog=10)
    server.close()
    return 0


asyncio.run(main())
""",
    )
    assert "PS2003" in stderr


def test_the_unknown_server_method_fences(tmp_path: Path) -> None:
    """`start_serving` and the rest of CPython's Server fence with PS2001."""
    stderr = _fence(
        tmp_path,
        "start_serving",
        """
import asyncio


async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    pass


async def main() -> int:
    server: asyncio.Server = await asyncio.start_server(handler, "127.0.0.1", 0)
    await server.start_serving()
    server.close()
    return 0


asyncio.run(main())
""",
    )
    assert "PS2001" in stderr
    assert "Server.start_serving" in stderr


def test_a_wrong_callback_shape_fences(tmp_path: Path) -> None:
    """The callback takes the (reader, writer) pair: another shape refuses."""
    stderr = _fence(
        tmp_path,
        "wrong_cb",
        """
import asyncio


async def handler(reader: asyncio.StreamReader) -> None:
    pass


async def main() -> int:
    server: asyncio.Server = await asyncio.start_server(handler, "127.0.0.1", 0)
    server.close()
    return 0


asyncio.run(main())
""",
    )
    # The checker preflight fences the shape first; the message names the
    # missing StreamWriter parameter.
    assert "StreamWriter" in stderr
