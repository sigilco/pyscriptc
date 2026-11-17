# SPDX-License-Identifier: Apache-2.0
"""socket: the closed blocking TCP surface (issue #1270).

The subset is `socket.socket(AF_INET, SOCK_STREAM)` over the loopback:
`bind`, `listen`, `accept`, `connect`, `recv`, `send`, `sendall`, `close`,
`setsockopt(SO_REUSEADDR)`, `getsockname`, `fileno` and the context-manager
form. The compiled output must match CPython byte for byte; the port
numbers differ between the runs, so the programs print facts about them.

The fences are the ones the closed subset draws: IPv6, UDP, `makefile`,
non-blocking mode and the computed family refuse with PS2001, and a
computed endpoint refuses with PS1003.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import compile_run, write_case

from pyscriptc.lower import lower_source

ECHO = """\
import socket


def run() -> int:
    server: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    bound = server.getsockname()
    print("bound host", bound[0] == "127.0.0.1")
    print("bound port", bound[1] > 0)

    client: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(("127.0.0.1", bound[1]))

    conn, peer = server.accept()
    print("peer host", peer[0] == "127.0.0.1")
    print("peer port", peer[1] > 0)

    client.sendall(b"hello tcp")
    received: bytes = conn.recv(64)
    print("received", received == b"hello tcp")

    conn.sendall(b"echo ok")
    echoed: bytes = client.recv(64)
    print("echoed", echoed == b"echo ok")

    sent: int = client.send(b"short")
    print("sent", sent == 5)

    fd: int = conn.fileno()
    print("fd", fd >= 0)

    conn.close()
    client.close()
    server.close()
    return 0
"""

WITH_FORM = """\
import socket


def run() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        bound = server.getsockname()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.connect(("127.0.0.1", bound[1]))
            conn, peer = server.accept()
            client.sendall(b"hi")
            got: bytes = conn.recv(10)
            print("got", got == b"hi")
            conn.close()
    return 0
"""

HOSTNAME = """\
import socket


def run() -> int:
    host: str = socket.gethostname()
    print("hostname", len(host) > 0)
    return 0
"""


def _oracle_bytes(tmp_path: Path, name: str, source: str, island: bool = False) -> str:
    """The compiled output must equal the CPython oracle's output.

    The TCP surface is native, so its binary links no libpython and the
    link audit stays strict. `gethostname` rides the island (the host's
    socket module answers it), so that one binary links the host's
    libpython and the audit accepts it on purpose, the way the call
    table's host lane does.
    """
    path = write_case(tmp_path, name, source)
    runner = (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('mod', r'{path}')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "print(mod.run())\n"
    )
    expected = subprocess.run(
        [sys.executable, "-c", runner], capture_output=True, text=True, encoding="latin-1", check=True
    ).stdout
    extra = ["--allow-host-libpython"] if island else []
    got = compile_run(tmp_path, path, "run", extra=extra)
    assert got.returncode == 0, got.stderr
    assert got.stdout == expected
    return got.stdout


def _fence(source: str, code: str, message: str) -> None:
    mod = lower_source(source)
    fences = mod["fences"]
    assert fences, source
    assert fences[0]["code"] == code, fences
    assert message in fences[0]["message"], fences


def test_the_tcp_echo_matches_cpython(tmp_path: Path) -> None:
    out = _oracle_bytes(tmp_path, "echo.py", ECHO)
    assert out == (
        "bound host True\n"
        "bound port True\n"
        "peer host True\n"
        "peer port True\n"
        "received True\n"
        "echoed True\n"
        "sent True\n"
        "fd True\n"
        "0\n"
    )


def test_the_context_manager_form_matches_cpython(tmp_path: Path) -> None:
    out = _oracle_bytes(tmp_path, "with.py", WITH_FORM)
    assert out == "got True\n0\n"


def test_gethostname_matches_cpython(tmp_path: Path) -> None:
    out = _oracle_bytes(tmp_path, "hostname.py", HOSTNAME, island=True)
    assert out == "hostname True\n0\n"


def test_ipv6_fences() -> None:
    _fence(
        "import socket\n\nx: socket.socket = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)\n",
        "PS2001",
        "AF_INET only",
    )


def test_udp_fences() -> None:
    _fence(
        "import socket\n\nx: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n",
        "PS2001",
        "SOCK_STREAM only",
    )


def test_the_computed_family_fences() -> None:
    _fence(
        "import socket\n\n"
        "def run() -> int:\n"
        "    family: int = 1 + 1\n"
        "    x: socket.socket = socket.socket(family, socket.SOCK_STREAM)\n"
        "    return 0\n",
        "PS1003",
        "socket.socket family is a constant int",
    )


def test_udp_methods_fence() -> None:
    _fence(
        "import socket\n\n"
        "def run() -> int:\n"
        "    s: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "    s.sendto(b'x', ('127.0.0.1', 9))\n"
        "    return 0\n",
        "PS2001",
        "is UDP",
    )


def test_non_blocking_fences() -> None:
    _fence(
        "import socket\n\n"
        "def run() -> int:\n"
        "    s: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "    s.setblocking(False)\n"
        "    return 0\n",
        "PS2001",
        "is non-blocking mode",
    )


def test_makefile_fences() -> None:
    _fence(
        "import socket\n\n"
        "def run() -> int:\n"
        "    s: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "    f = s.makefile('rb')\n"
        "    return 0\n",
        "PS2001",
        "not in the lowered subset of socket",
    )


def test_the_unknown_module_function_fences() -> None:
    _fence(
        "import socket\n\nx: int = socket.gethostbyname('localhost')\n",
        "PS2001",
        "not in the lowered subset of socket",
    )


def test_the_computed_endpoint_fences() -> None:
    _fence(
        "import socket\n\n"
        "def run() -> int:\n"
        "    host: int = 5\n"
        "    s: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "    s.bind((host, 0))\n"
        "    return 0\n",
        "PS1003",
        "bind host is str",
    )


def test_the_wrong_port_type_fences() -> None:
    _fence(
        "import socket\n\n"
        "def run() -> int:\n"
        "    port: str = 'x'\n"
        "    s: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "    s.bind(('127.0.0.1', port))\n"
        "    return 0\n",
        "PS1003",
        "bind port is int",
    )


def test_the_non_tuple_endpoint_fences() -> None:
    _fence(
        "import socket\n\n"
        "def run() -> int:\n"
        "    s: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "    s.bind('127.0.0.1')\n"
        "    return 0\n",
        "PS1003",
        "bind takes a (host, port) tuple",
    )
