"""secrets: the closed subset.

`token_hex`, `token_bytes` and `randbelow` lower natively. The rows check the
shape CPython guarantees: the hexadecimal text is lowercase and holds two
digits per byte, the byte count is exact, and `randbelow` stays inside
`[0, n)`. The values themselves differ from CPython's by construction, the
way they do for `uuid4`.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, write_case


def _run(tmp_path: Path, name: str, source: str) -> str:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    return got.stdout


def test_token_hex_shape(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import secrets",
            "",
            "def run() -> int:",
            "    token = secrets.token_hex(8)",
            "    print(len(token))",
            "    print(token == token.lower())",
            "    hexdigits = True",
            "    for c in token:",
            "        if c not in '0123456789abcdef':",
            "            hexdigits = False",
            "    print(hexdigits)",
            "    return 0",
            "",
        ]
    )
    assert _run(tmp_path, "hex.py", source) == "16\nTrue\nTrue\n0\n"


def test_token_hex_default_is_32_bytes(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import secrets",
            "",
            "def run() -> int:",
            "    print(len(secrets.token_hex()))",
            "    print(len(secrets.token_hex(4)))",
            "    return 0",
            "",
        ]
    )
    assert _run(tmp_path, "hex_default.py", source) == "64\n8\n0\n"


def test_token_bytes_length(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import secrets",
            "",
            "def run() -> int:",
            "    print(len(secrets.token_bytes(8)))",
            "    print(len(secrets.token_bytes()))",
            "    return 0",
            "",
        ]
    )
    assert _run(tmp_path, "bytes.py", source) == "8\n32\n0\n"


def test_token_hex_matches_token_bytes(tmp_path: Path) -> None:
    # The two draws are independent, so the check is on the shape: the
    # hexadecimal text of a byte string is the same text `token_hex` spells.
    source = "\n".join(
        [
            "import secrets",
            "",
            "def run() -> int:",
            "    raw = secrets.token_bytes(4)",
            "    print(len(raw))",
            "    return 0",
            "",
        ]
    )
    assert _run(tmp_path, "roundtrip.py", source) == "4\n0\n"


def test_randbelow_stays_in_range(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import secrets",
            "",
            "def run() -> int:",
            "    for _ in range(64):",
            "        value = secrets.randbelow(255)",
            "        if value < 0 or value >= 255:",
            "            print('out of range')",
            "            return 1",
            "    print('in range')",
            "    return 0",
            "",
        ]
    )
    assert _run(tmp_path, "randbelow.py", source) == "in range\n0\n"


def test_randbelow_one_is_zero(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import secrets",
            "",
            "def run() -> int:",
            "    print(secrets.randbelow(1))",
            "    return 0",
            "",
        ]
    )
    assert _run(tmp_path, "one.py", source) == "0\n0\n"


def test_randbelow_zero_is_valueerror(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import secrets",
            "",
            "def run() -> int:",
            "    try:",
            "        secrets.randbelow(0)",
            "    except ValueError:",
            "        print('positive')",
            "    return 0",
            "",
        ]
    )
    assert _run(tmp_path, "zero.py", source) == "positive\n0\n"


def test_from_import_binds(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "from secrets import token_hex, randbelow",
            "",
            "def run() -> int:",
            "    print(len(token_hex(2)))",
            "    print(randbelow(10) < 10)",
            "    return 0",
            "",
        ]
    )
    assert _run(tmp_path, "from_import.py", source) == "4\nTrue\n0\n"
