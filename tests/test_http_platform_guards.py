"""urllib.request on unsupported targets: wasm32-wasi and windows-gnu link.

The runtime links every runtime C file into every binary, so before the
platform guards `ps_http.c` (BSD sockets, `arpa/inet.h`) broke the link for
any program on a WASI or Windows target. The guards stub the HTTP client
there: a program that never calls `urlopen` links, one that does raises
URLError (OSError) at run time with a message naming the target family.
The host build is untouched, which the loopback suite in
`test_urllib_request.py` keeps proving. Issue #313 (wasm), #249/#283
(windows).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

from pyscriptc.emit.llvm._driver import link_binary

ZIG = shutil.which("zig")

HELLO = """
def main() -> int:
    print("hello")
    return 0
"""

URLOPEN = """
import urllib.request

def f(url: str) -> int:
    r: urllib.request.HTTPResponse = urllib.request.urlopen(url)
    return r.getcode()
"""


def _compile(source: str, name: str, target: str, tmp_path: Path) -> Path:
    path = write_case(tmp_path, name, source)
    proc = run_cli(
        "compile",
        str(path),
        "--target",
        target,
        "--driver",
        "zig",
        "--emit",
        "bin",
        "-o",
        str(tmp_path / (name + ".out")),
    )
    assert proc.returncode == 0, proc.stderr
    out = tmp_path / (name + ".out")
    assert out.is_file()
    return out


@pytest.mark.skipif(ZIG is None, reason="zig not on PATH")
def test_hello_builds_for_wasm32_wasi(tmp_path: Path) -> None:
    out = _compile(HELLO, "hello.py", "wasm32-wasi", tmp_path)
    assert out.read_bytes()[:4] == b"\0asm"


@pytest.mark.skipif(ZIG is None, reason="zig not on PATH")
def test_hello_builds_for_windows_gnu(tmp_path: Path) -> None:
    out = _compile(HELLO, "hello.py", "x86_64-windows-gnu", tmp_path)
    assert out.read_bytes()[:2] == b"MZ"


def test_urlopen_ir_links_host_with_the_stub_untouched(tmp_path: Path) -> None:
    # The native path keeps the real socket client: the same IR links
    # against the host clang with no stub symbols in the way.
    path = write_case(tmp_path, "url.py", URLOPEN)
    proc = run_cli("compile", str(path), "--emit", "llvm", "-o", str(tmp_path / "url.ll"))
    assert proc.returncode == 0, proc.stderr
    ll = tmp_path / "url.ll"
    link_binary(ll, tmp_path / "url", driver="clang")
    assert (tmp_path / "url").is_file()


@pytest.mark.skipif(ZIG is None, reason="zig not on PATH")
def test_stub_uses_the_wasi_branch(tmp_path: Path) -> None:
    # The stub branch, not the socket branch, is what compiles for WASI:
    # the socket headers stay out of the object, and the diagnostic text
    # for the wasi family is in it.
    path = write_case(tmp_path, "hello.py", HELLO)
    obj = tmp_path / "ps_http.o"
    proc = subprocess.run(
        [
            ZIG,
            "cc",
            "-target",
            "wasm32-wasi",
            "-O2",
            "-I",
            "src/pyscriptc/runtime",
            "-c",
            "src/pyscriptc/runtime/ps_http.c",
            "-o",
            str(obj),
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert proc.returncode == 0, proc.stderr
    data = obj.read_bytes()
    assert b"not supported on the wasi target" in data
    assert b"connect" not in data
