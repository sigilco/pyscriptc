# SPDX-License-Identifier: Apache-2.0
"""UTF-8 text I/O: stdin, stdout, files, `sys.argv` and the environment (#478).

The text I/O default is UTF-8 (PEP 540, PEP 686). The committed probes
`probes/differential/utf8_text_io.py` and
`probes/differential/utf8_stdin_argv_env.py` are the AGENTS.md
differential-probe row for the runtime change; the tests here replay them
against CPython byte for byte and pin the boundaries the probes cannot
feed: a non-ASCII argument, a non-ASCII environment value, a stdin payload,
and bytes that are not valid UTF-8 (a named UnicodeDecodeError, never a
silent replacement).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import ROOT

from pyscriptc.lower import lower_source

ZIG = shutil.which("zig") is not None
pytestmark = pytest.mark.skipif(not ZIG, reason="zig not on PATH")

ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1"}
ORACLE_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def _oracle() -> str:
    """The pinned CPython 3.14 interpreter, else the first 3.14 on PATH, else this one."""
    pinned = sorted((Path.home() / ".local/share/uv/python").glob("cpython-3.14.*-*/bin/python3"))
    if pinned:
        return str(pinned[-1])
    return shutil.which("python3.14") or sys.executable


def _build(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    binary = tmp_path / f"{name}.bin"
    proc = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(path), "--emit", "bin", "-o", str(binary)],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return binary


def _run(
    cmd: list[str], cwd: Path, *, stdin: bytes = b"", env: dict[str, str] | None = None
) -> tuple[bytes, bytes, int]:
    run = subprocess.run(cmd, input=stdin, capture_output=True, timeout=120, cwd=cwd, env=env or ORACLE_ENV)
    return run.stdout, run.stderr, run.returncode


def _both(
    tmp_path: Path,
    name: str,
    source: str,
    *,
    args: list[str] | None = None,
    stdin: bytes = b"",
    extra_env: dict[str, str] | None = None,
) -> tuple[tuple[bytes, bytes, int], tuple[bytes, bytes, int]]:
    """Run the compiled program and CPython on one source: (native, oracle)."""
    binary = _build(tmp_path, name, source)
    env = {**ORACLE_ENV, **(extra_env or {})}
    native_dir = tmp_path / "native"
    oracle_dir = tmp_path / "oracle"
    native_dir.mkdir(exist_ok=True)
    oracle_dir.mkdir(exist_ok=True)
    rest = args or []
    native = _run([str(binary), *rest], native_dir, stdin=stdin, env=env)
    oracle = _run([_oracle(), str(tmp_path / f"{name}.py"), *rest], oracle_dir, stdin=stdin, env=env)
    return native, oracle


@pytest.mark.parametrize("probe", ["utf8_text_io", "utf8_stdin_argv_env"])
def test_the_committed_probes_match_cpython(tmp_path: Path, probe: str) -> None:
    source = (ROOT / "probes" / "differential" / f"{probe}.py").read_text(encoding="utf-8")
    stdin = "h\u00e9llo \U0001f600\nsecond \u00fc\u65e5\r\nx".encode()
    native, oracle = _both(
        tmp_path,
        probe,
        source,
        args=["\u00f1and\u00fa \U0001f600"],
        stdin=stdin,
        extra_env={"PYSC_PROBE_ENV": "b\u00e4r \U0001f600"},
    )
    assert native == oracle


def test_print_cafe_is_the_utf8_bytes(tmp_path: Path) -> None:
    """The byte-level receipt: `print("café")` is 63 61 66 c3 a9 and a newline."""
    native, oracle = _both(tmp_path, "cafe", 'print("caf\\u00e9")\n')
    assert native[0] == bytes([0x63, 0x61, 0x66, 0xC3, 0xA9, 0x0A])
    assert native == oracle


def test_a_code_point_above_ffff_prints_as_four_bytes(tmp_path: Path) -> None:
    native, oracle = _both(tmp_path, "astral", 's: str = "\\U0001f600"\nprint(s)\nprint(len(s))\n')
    assert native[0] == b"\xf0\x9f\x98\x80\n1\n"
    assert native == oracle


def test_file_round_trip_is_byte_identical_to_cpython(tmp_path: Path) -> None:
    source = """\
payload: str = "h\\u00e9llo w\\u00f6rld \\U0001f600 \\u65e5\\u672c\\n"
with open("rt.txt", "w") as out:
    out.write(payload)
with open("rt.txt", "rb") as raw:
    print(raw.read())
with open("rt.txt") as fh:
    back: str = fh.read()
print(back == payload, len(back))
"""
    native, oracle = _both(tmp_path, "roundtrip", source)
    assert native == oracle
    assert (tmp_path / "native" / "rt.txt").read_bytes() == (tmp_path / "oracle" / "rt.txt").read_bytes()
    assert (tmp_path / "native" / "rt.txt").read_bytes() == "h\u00e9llo w\u00f6rld \U0001f600 \u65e5\u672c\n".encode()


def test_argv_with_a_non_ascii_argument(tmp_path: Path) -> None:
    source = "import sys\nfor a in sys.argv[1:]:\n    print(repr(a), len(a))\n"
    args = ["\u00f1and\u00fa", "\u65e5\u672c\U0001f600", ""]
    native, oracle = _both(tmp_path, "argv", source, args=args)
    assert native == oracle
    assert "\u00f1and\u00fa".encode() in native[0]


def test_environ_decodes_utf8(tmp_path: Path) -> None:
    source = (
        'import os\nv: str = os.environ.get("PYSC_T_ENV", "none")\nprint(v, len(v))\nprint(os.environ["PYSC_T_ENV"])\n'
    )
    native, oracle = _both(tmp_path, "env", source, extra_env={"PYSC_T_ENV": "b\u00e4r \U0001f600"})
    assert native == oracle
    assert native[0].startswith("b\u00e4r \U0001f600 5\n".encode())


def test_input_reads_a_utf8_line_once_per_assignment(tmp_path: Path) -> None:
    """`a: str = input()` at module scope runs once, however many times `a` is read."""
    source = "a: str = input()\nprint(repr(a), len(a))\nb: str = input()\nprint(repr(b), len(b), b.upper())\n"
    stdin = "h\u00e9llo \U0001f600\nprompt \u00fc\r\nlast".encode()
    native, oracle = _both(tmp_path, "input", source, stdin=stdin)
    assert native == oracle
    assert native[0].count(b"\n") == 2


def test_stdin_readline_decodes_utf8(tmp_path: Path) -> None:
    source = (
        "import sys\n"
        "line: str = sys.stdin.readline()\n"
        "print(repr(line), len(line))\n"
        "rest: str = sys.stdin.readline()\n"
        "print(repr(rest), len(rest))\n"
    )
    stdin = "\u65e5\u672c\U0001f600\n\u00fc".encode()
    native, oracle = _both(tmp_path, "readline", source, stdin=stdin)
    assert native == oracle


def test_write_returns_code_points(tmp_path: Path) -> None:
    source = (
        "import sys\n"
        'with open("w.txt", "w") as f:\n'
        '    n: int = f.write("\\u00e9\\U0001f600")\n'
        "print(n)\n"
        'm: int = sys.stdout.write("\\u00e9\\n")\n'
        "print(m)\n"
    )
    native, oracle = _both(tmp_path, "writecount", source)
    assert native == oracle
    assert native[0] == "2\n\u00e9\n2\n".encode()


def test_invalid_utf8_stdin_is_a_named_error(tmp_path: Path) -> None:
    source = (
        'try:\n    s: str = input()\n    print(s)\nexcept UnicodeDecodeError as e:\n    print("UnicodeDecodeError")\n'
    )
    binary = _build(tmp_path, "badstdin", source)
    out, _err, rc = _run([str(binary)], tmp_path, stdin=b"ab\xffcd\n")
    assert out == b"UnicodeDecodeError\n"
    assert rc == 0


def test_invalid_utf8_argv_is_a_named_error_not_a_replacement(tmp_path: Path) -> None:
    """The OS gives bytes; an argument that is not UTF-8 never decodes to U+FFFD."""
    binary = _build(tmp_path, "badargv", "import sys\nprint(sys.argv[1])\n")
    proc = subprocess.run([str(binary), b"a\x80b"], capture_output=True, timeout=120, cwd=tmp_path)
    assert proc.returncode != 0
    assert proc.stdout == b""
    assert b"UnicodeDecodeError" in proc.stderr
    assert b"sys.argv[1]" in proc.stderr
    assert b"\xef\xbf\xbd" not in proc.stdout + proc.stderr


def test_a_program_that_never_reads_argv_ignores_a_bad_argument(tmp_path: Path) -> None:
    binary = _build(tmp_path, "noargv", 'print("ok")\n')
    proc = subprocess.run([str(binary), b"a\x80b"], capture_output=True, timeout=120, cwd=tmp_path)
    assert (proc.stdout, proc.returncode) == (b"ok\n", 0)


def test_invalid_utf8_environment_value_is_a_named_error(tmp_path: Path) -> None:
    source = (
        "import os\n"
        "try:\n"
        '    v: str = os.environ.get("PYSC_BAD", "x")\n'
        "    print(v)\n"
        "except UnicodeDecodeError:\n"
        '    print("UnicodeDecodeError")\n'
    )
    binary = _build(tmp_path, "badenv", source)
    raw_env = {k.encode(): v.encode() for k, v in ORACLE_ENV.items()}
    raw_env[b"PYSC_BAD"] = b"x\xc3("
    bad = subprocess.run([str(binary)], capture_output=True, timeout=120, cwd=tmp_path, env=raw_env)  # type: ignore[arg-type]
    assert bad.stdout == b"UnicodeDecodeError\n"


def test_text_read_of_invalid_utf8_raises(tmp_path: Path) -> None:
    source = (
        'with open("bad.txt", "wb") as raw:\n'
        '    raw.write(b"ab\\xff\\xfe")\n'
        "try:\n"
        '    with open("bad.txt") as fh:\n'
        "        print(fh.read())\n"
        "except UnicodeDecodeError:\n"
        '    print("UnicodeDecodeError")\n'
    )
    native, oracle = _both(tmp_path, "badfile", source)
    assert native == oracle
    assert native[0] == b"UnicodeDecodeError\n"


def _fences(source: str) -> list[dict[str, str]]:
    return list(lower_source(source)["fences"])


@pytest.mark.parametrize("name", ["utf-8", "UTF_8", "utf8", "ascii", "us-ascii", "latin-1", "Latin_1", "iso-8859-1"])
def test_open_honors_the_named_codecs(name: str) -> None:
    src = f"with open('a', encoding={name!r}) as h:\n    s: str = h.read()\n"
    assert _fences(src) == []


@pytest.mark.parametrize("name", ["utf-16", "cp1252", "utf-8-sig", "gbk"])
def test_open_fences_a_codec_it_does_not_honor(name: str) -> None:
    src = f"with open('a', encoding={name!r}) as h:\n    s: str = h.read()\n"
    fences = _fences(src)
    assert fences and fences[0]["code"] == "PS2001"
    assert "utf-8, ascii, or latin-1" in fences[0]["message"]


def test_open_binary_mode_refuses_an_encoding() -> None:
    fences = _fences("with open('a', 'rb', encoding='latin-1') as h:\n    b: bytes = h.read()\n")
    assert fences and fences[0]["code"] == "PS2001"
    assert "binary mode" in fences[0]["message"]
