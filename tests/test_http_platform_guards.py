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

ZIG = shutil.which("zig")
ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "src" / "pyscriptc" / "runtime"

HELLO = """
def main() -> int:
    print("hello")
    return 0
"""

URLOPEN = """
import urllib.request

def getcode(url: str) -> int:
    r: urllib.request.HTTPResponse = urllib.request.urlopen(url)
    return r.getcode()

def main() -> int:
    return 0
"""

WASI = "wasm32-wasi"
WINDOWS = "x86_64-windows-gnu"


def _compile_http_unit(target: str, tmp_path: Path) -> Path:
    """Compile the HTTP client unit alone for `target`."""
    obj = tmp_path / f"ps_http-{target}.o"
    proc = subprocess.run(
        [
            ZIG,
            "cc",
            "-target",
            target,
            "-O2",
            "-Wall",
            "-I",
            str(RUNTIME),
            "-c",
            str(RUNTIME / "ps_http.c"),
            "-o",
            str(obj),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return obj


def _link_hello(target: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    path = write_case(tmp_path, f"hello-{target}.py", HELLO)
    return run_cli(
        "compile",
        str(path),
        "--target",
        target,
        "--driver",
        "zig",
        "--emit",
        "bin",
        "-o",
        str(tmp_path / f"hello-{target}.out"),
    )


@pytest.mark.skipif(ZIG is None, reason="zig not on PATH")
@pytest.mark.parametrize("target", [WASI, WINDOWS])
def test_http_unit_compiles_for_target(target: str, tmp_path: Path) -> None:
    # The socket headers are the failure this guards: without the platform
    # guards the unit failed to compile with `socket`/`connect` undeclared
    # (wasi-libc) and `arpa/inet.h` missing (windows).
    obj = _compile_http_unit(target, tmp_path)
    assert obj.is_file()


@pytest.mark.skipif(ZIG is None, reason="zig not on PATH")
@pytest.mark.parametrize(
    ("target", "family"),
    [(WASI, b"not supported on the wasi target"), (WINDOWS, b"not supported on the windows target")],
)
def test_stub_branch_is_the_one_compiled(target: str, family: bytes, tmp_path: Path) -> None:
    # The stub, not the socket client, is what compiles for these targets:
    # the diagnostic names the target family, and no socket call is in the
    # object.
    obj = _compile_http_unit(target, tmp_path)
    data = obj.read_bytes()
    assert family in data
    assert b"connect" not in data


@pytest.mark.skipif(ZIG is None, reason="zig not on PATH")
@pytest.mark.parametrize("target", [WASI, WINDOWS])
def test_hello_world_link_has_no_http_failure(target: str, tmp_path: Path) -> None:
    # A hello world must not fail because of the HTTP client. Other runtime
    # units may still refuse the target (the wasm and windows branches own
    # those), so the check is that no ps_http diagnostic appears: the HTTP
    # client is out of the way for these targets.
    proc = _link_hello(target, tmp_path)
    if proc.returncode != 0:
        assert "ps_http" not in proc.stderr, proc.stderr
        assert "arpa/inet.h" not in proc.stderr, proc.stderr
        assert "undeclared function 'socket'" not in proc.stderr, proc.stderr
        pytest.skip(f"the {target} link still needs the other runtime units: {proc.stderr.splitlines()[-1]}")
    out = tmp_path / f"hello-{target}.out"
    assert out.is_file()


@pytest.mark.skipif(ZIG is None, reason="zig not on PATH")
@pytest.mark.parametrize("target", [WASI, WINDOWS])
def test_urlopen_program_has_no_http_failure(target: str, tmp_path: Path) -> None:
    # A program that uses urllib.request must reach the compiler, and the
    # link must not trip over the socket client.
    path = write_case(tmp_path, f"url-{target}.py", URLOPEN)
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
        str(tmp_path / f"url-{target}.out"),
    )
    if proc.returncode != 0:
        assert "ps_http" not in proc.stderr, proc.stderr
        assert "arpa/inet.h" not in proc.stderr, proc.stderr
        pytest.skip(f"the {target} link still needs the other runtime units: {proc.stderr.splitlines()[-1]}")
    assert (tmp_path / f"url-{target}.out").is_file()


def test_urlopen_ir_links_on_the_host(tmp_path: Path) -> None:
    # The native path keeps the real socket client: IR that calls into
    # ps_http links against the host clang with no stub in the way.
    path = write_case(
        tmp_path,
        "url.py",
        URLOPEN + '\n\ngetcode("http://127.0.0.1:9/")\n',
    )
    proc = run_cli("compile", str(path), "--emit", "bin", "-o", str(tmp_path / "url.out"))
    assert proc.returncode == 0, proc.stderr
    ll = tmp_path / "url.out.ll"
    assert ll.is_file()
    # The emitted IR calls ps_http_request: the host link resolved it from
    # the real socket client, so the stub never gets in the way.
    assert "ps_http_request" in ll.read_text(encoding="utf-8")
    assert (tmp_path / "url.out").is_file()
