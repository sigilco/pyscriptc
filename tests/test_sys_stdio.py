# SPDX-License-Identifier: Apache-2.0
"""`sys.stdin.readline` and the `sys.stdout.flush` / `sys.stderr.flush`
no-ops over the lowered sys surface (issue #1225).

The lowered `sys` surface carried `stdout.write` and `stderr.write` only,
so the two stdio calls a line-oriented protocol needs fenced with PS2001:
`sys.stdin.readline` (the natural line read) and `sys.stdout.flush` (what
makes a written response reach the peer on a pipe).

CPython is the oracle for the semantics. On POSIX CPython opens `sys.stdin`
with newline="\\n", so a line ends at a "\\n", the "\\n" stays in the returned
string, a "\\r" is an ordinary character, a line that ends at EOF without a
newline comes back whole, and a read at EOF returns the empty string. The
flushes return None and do not change the byte stream.

Each case compiles the program, runs it with a payload on stdin next to the
pinned CPython 3.14, and byte-compares stdout, stderr and the exit code.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import run_cli, write_case

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "probes" / "differential" / "sys_stdio.py"


def _differential(tmp_path: Path, name: str, source: str, payload: bytes) -> subprocess.CompletedProcess[str]:
    """Compile the program, run it with `payload` on stdin, and pin the bytes."""
    path = write_case(tmp_path, name, source)
    binary = tmp_path / "prog.bin"
    compiled = run_cli("compile", str(path), "--emit", "bin", "-o", str(binary))
    assert compiled.returncode == 0, compiled.stderr
    native = subprocess.run(
        [str(binary)],
        input=payload,
        cwd=tmp_path,
        capture_output=True,
        timeout=60,
    )
    oracle = subprocess.run(
        [sys.executable, str(path)],
        input=payload,
        cwd=tmp_path,
        capture_output=True,
        timeout=60,
    )
    assert (native.returncode, native.stdout, native.stderr) == (
        oracle.returncode,
        oracle.stdout,
        oracle.stderr,
    ), f"native={native.stdout!r}/{native.stderr!r} cpython={oracle.stdout!r}/{oracle.stderr!r}"
    return native


def test_readline_returns_the_line_with_its_newline(tmp_path: Path) -> None:
    # CPython keeps the trailing newline: `readline` splits at "\n" and the
    # "\n" rides out with the line.
    got = _differential(
        tmp_path,
        "readline.py",
        "\n".join(
            [
                "import sys",
                "line: str = sys.stdin.readline()",
                "print(repr(line))",
                "",
            ]
        ),
        b"alpha\nbeta\n",
    )
    assert got.stdout == b"'alpha\\n'\n"


def test_readline_at_eof_returns_the_empty_string(tmp_path: Path) -> None:
    got = _differential(
        tmp_path,
        "readline_eof.py",
        "\n".join(
            [
                "import sys",
                "print(repr(sys.stdin.readline()))",
                "print(repr(sys.stdin.readline()))",
                "",
            ]
        ),
        b"",
    )
    assert got.stdout == b"''\n''\n"


def test_readline_returns_a_last_line_without_a_newline(tmp_path: Path) -> None:
    # The line that ends at EOF without a newline comes back whole.
    got = _differential(
        tmp_path,
        "readline_partial.py",
        "\n".join(
            [
                "import sys",
                "print(repr(sys.stdin.readline()))",
                "print(repr(sys.stdin.readline()))",
                "",
            ]
        ),
        b"alpha\nbeta",
    )
    assert got.stdout == b"'alpha\\n'\n'beta'\n"


def test_readline_keeps_a_carriage_return_as_an_ordinary_character(tmp_path: Path) -> None:
    # POSIX CPython splits stdin at "\n" only: a "\r" does not end the line.
    got = _differential(
        tmp_path,
        "readline_cr.py",
        "\n".join(
            [
                "import sys",
                "print(repr(sys.stdin.readline()))",
                "",
            ]
        ),
        b"alpha\rbeta\n",
    )
    assert got.stdout == b"'alpha\\rbeta\\n'\n"


def test_flush_returns_none_and_does_not_change_the_bytes(tmp_path: Path) -> None:
    got = _differential(
        tmp_path,
        "flush.py",
        "\n".join(
            [
                "import sys",
                "print(sys.stdout.flush() is None)",
                "print(sys.stderr.flush() is None)",
                "sys.stdout.write('payload\\n')",
                "sys.stdout.flush()",
                "",
            ]
        ),
        b"",
    )
    assert got.stdout == b"True\nTrue\npayload\n"


def test_the_committed_probe_matches_cpython(tmp_path: Path) -> None:
    # The differential probe under probes/differential/ (the AGENTS.md rule):
    # the compiled program prints what CPython prints, byte for byte.
    binary = tmp_path / "probe.bin"
    compiled = run_cli("compile", str(PROBE), "--emit", "bin", "-o", str(binary))
    assert compiled.returncode == 0, compiled.stderr
    payload = b"alpha\nbeta\ngamma"
    native = subprocess.run([str(binary)], input=payload, cwd=tmp_path, capture_output=True, timeout=60)
    oracle = subprocess.run([sys.executable, str(PROBE)], input=payload, cwd=tmp_path, capture_output=True, timeout=60)
    assert (native.returncode, native.stdout, native.stderr) == (
        oracle.returncode,
        oracle.stdout,
        oracle.stderr,
    )
    assert native.stdout == b"first: alpha\nsecond: beta\nthird: gammaeof: []\n"
