"""The codec wave lowers a closed subset and still fences names outside that surface."""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import EXAMPLES, compile_run, oracle, run_cli, write_case


def test_wave_probe_matches_cpython(tmp_path: Path) -> None:
    path = EXAMPLES / "wave_probe.py"
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout == "0\n"


def test_from_import_of_a_lowered_function(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "sha.py",
        "\n".join(
            [
                "from hashlib import sha256",
                "def f() -> str:",
                "    return sha256(b'abc').hexdigest()",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "f")


def test_codec_fences_stay_closed(tmp_path: Path) -> None:
    cases = [
        (
            "search.py",
            "import re\n\ndef f(s: str) -> int:\n    re.search('a', s)\n    return 1\n",
            "PS2001",
            "re.search",
        ),
        (
            "load.py",
            "import json\n\ndef f(s: str) -> int:\n    json.load(s)  # type: ignore[arg-type]\n    return 1\n",
            "PS2001",
            "json.load",
        ),
        (
            "request.py",
            "\n".join(
                [
                    "import urllib",
                    "def f() -> int:",
                    "    urllib.request.urlopen('http://example')  # type: ignore[attr-defined]",
                    "    return 1",
                    "",
                ]
            ),
            "PS2001",
            "urllib.request.urlopen",
        ),
        (
            "encodebytes.py",
            "import base64\n\ndef f(b: bytes) -> bytes:\n    return base64.encodebytes(b)\n",
            "PS2001",
            "base64.encodebytes",
        ),
        (
            "sha3.py",
            "import hashlib\n\ndef f(b: bytes) -> str:\n    return hashlib.sha3_256(b).hexdigest()\n",
            "PS2001",
            "hashlib.sha3_256",
        ),
        (
            "calcsize.py",
            "import struct\n\ndef f() -> int:\n    return struct.calcsize('<H')\n",
            "PS2001",
            "struct.calcsize",
        ),
        (
            "groups.py",
            "import re\n\ndef f() -> str:\n    return re.findall(r'(a)(b)(c)(d)(e)', 'abcde')[0]\n",
            "PS2002",
            "four",
        ),
        (
            "plain.py",
            "import json\n\ndef f(s: str) -> int:\n    json.loads(s)\n    return 1\n",
            "PS1001",
            "json.loads needs the result annotation",
        ),
        (
            "floatjson.py",
            "import json\n\ndef f() -> str:\n    return json.dumps(1.5)\n",
            "PS2001",
            "float JSON",
        ),
        (
            "dynfmt.py",
            "import struct\n\ndef f(fmt: str, data: bytes) -> int:\n    return struct.unpack(fmt, data)[0]\n",
            "PS2001",
            "string literal",
        ),
    ]
    for name, source, code, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert code in got.stderr
        assert text in got.stderr
