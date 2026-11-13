"""binascii: the closed subset.

`unhexlify` and `hexlify` lower natively. The deterministic rows diff against
the CPython oracle byte for byte, including the error cases: an odd-length
hex string and a non-hex character raise ValueError, which is what CPython's
`binascii.Error` reads as at an `except (ValueError, ...)` handler.
"""

from __future__ import annotations

import binascii as py_binascii
from pathlib import Path

from tests.test_gates import compile_run, write_case


def _run(tmp_path: Path, name: str, source: str) -> str:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    return got.stdout


def test_hexlify_matches_cpython(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import binascii",
            "",
            "def run() -> int:",
            "    print(binascii.hexlify(b'hello'))",
            "    print(binascii.hexlify(b''))",
            "    print(binascii.hexlify(b'\\x00\\xff\\x0a'))",
            "    return 0",
            "",
        ]
    )
    expected = "\n".join(
        [
            str(py_binascii.hexlify(b"hello")),
            str(py_binascii.hexlify(b"")),
            str(py_binascii.hexlify(b"\x00\xff\x0a")),
        ]
    )
    assert _run(tmp_path, "hexlify.py", source) == expected + "\n0\n"


def test_unhexlify_matches_cpython(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import binascii",
            "",
            "def run() -> int:",
            "    print(binascii.unhexlify('68656c6c6f'))",
            "    print(binascii.unhexlify(''))",
            "    print(binascii.unhexlify('00ff0a'))",
            "    return 0",
            "",
        ]
    )
    expected = "\n".join(
        [
            str(py_binascii.unhexlify("68656c6c6f")),
            str(py_binascii.unhexlify("")),
            str(py_binascii.unhexlify("00ff0a")),
        ]
    )
    assert _run(tmp_path, "unhex.py", source) == expected + "\n0\n"


def test_unhexlify_roundtrip(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import binascii",
            "",
            "def run() -> int:",
            "    data = binascii.unhexlify('deadbeef')",
            "    print(binascii.hexlify(data))",
            "    return 0",
            "",
        ]
    )
    assert _run(tmp_path, "round.py", source) == "b'deadbeef'\n0\n"


def test_unhexlify_odd_length_is_valueerror(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import binascii",
            "",
            "def run() -> int:",
            "    try:",
            "        binascii.unhexlify('abc')",
            "    except ValueError:",
            "        print('odd')",
            "    return 0",
            "",
        ]
    )
    assert _run(tmp_path, "odd.py", source) == "odd\n0\n"


def test_unhexlify_non_hex_is_valueerror(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import binascii",
            "",
            "def run() -> int:",
            "    try:",
            "        binascii.unhexlify('zz')",
            "    except ValueError:",
            "        print('bad')",
            "    return 0",
            "",
        ]
    )
    assert _run(tmp_path, "bad.py", source) == "bad\n0\n"


def test_hexlify_accepts_str_is_refused(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import binascii",
            "",
            "def run() -> int:",
            "    try:",
            "        print(binascii.hexlify('hello'))",
            "    except ValueError:",
            "        print('refused')",
            "    return 0",
            "",
        ]
    )
    # CPython refuses a str argument to hexlify with TypeError; the lowered
    # subset fences it at compile time, so the case does not compile.
    path = write_case(tmp_path, "hexstr.py", source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
