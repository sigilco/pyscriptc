"""IO[bytes], IO[str], BinaryIO, and TextIO are the handles open() already returns."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = [
    "import typing",
    "import typing_extensions",
    "from typing import IO, BinaryIO, TextIO",
    "",
    "def from_io_str(handle: IO[str]) -> str:",
    "    return handle.read()",
    "",
    "def from_textio(handle: TextIO) -> str:",
    "    return handle.read()",
    "",
    "def from_io_bytes(handle: IO[bytes]) -> bytes:",
    "    return handle.read()",
    "",
    "def from_binaryio(handle: BinaryIO) -> bytes:",
    "    return handle.read()",
    "",
    "def from_typing_attr(handle: typing.IO[str]) -> str:",
    "    return handle.read()",
    "",
    "def from_ext_text(handle: typing_extensions.TextIO) -> str:",
    "    return handle.read()",
    "",
    "def from_ext_bin(handle: typing_extensions.BinaryIO) -> bytes:",
    "    return handle.read()",
    "",
    "def from_ext_io(handle: typing_extensions.IO[bytes]) -> bytes:",
    "    return handle.read()",
    "",
    "def probe() -> str:",
    "    path: str = {path}",
    "    with open(path, 'w') as out:",
    "        out.write('Ada')",
    "    text: str = ''",
    "    again: str = ''",
    "    third: str = ''",
    "    fourth: str = ''",
    "    with open(path, 'r') as handle:",
    "        text = from_io_str(handle)",
    "    with open(path, 'r') as handle:",
    "        again = from_textio(handle)",
    "    with open(path, 'r') as handle:",
    "        third = from_typing_attr(handle)",
    "    with open(path, 'r') as handle:",
    "        fourth = from_ext_text(handle)",
    "    mark: int = 0",
    "    with open(path, 'rb') as handle:",
    "        raw: bytes = from_io_bytes(handle)",
    "        mark = raw[0]",
    "    with open(path, 'rb') as handle:",
    "        raw = from_binaryio(handle)",
    "        mark = mark + raw[1]",
    "    with open(path, 'rb') as handle:",
    "        raw = from_ext_bin(handle)",
    "        mark = mark + raw[2]",
    "    with open(path, 'rb') as handle:",
    "        raw = from_ext_io(handle)",
    "        mark = mark + len(raw)",
    "    if text == 'Ada' and again == 'Ada' and third == 'Ada' and fourth == 'Ada' and mark == 265:",
    "        return text",
    "    return ''",
    "",
]


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_io_annotation_read_matches_cpython(tmp_path: Path) -> None:
    note = tmp_path / "note.txt"
    path = write_case(tmp_path, "io_ann.py", "\n".join(_PROBE).format(path=repr(str(note))))
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "Ada"


def test_io_annotation_fences(tmp_path: Path) -> None:
    cases = [
        (
            "bare.py",
            "from typing import IO\n\ndef read(handle: IO) -> str:\n    return handle.read()\n",
            "IO is not a lowered type in the IR",
        ),
        (
            "any.py",
            "from typing import IO, Any\n\ndef read(handle: IO[Any]) -> str:\n    return handle.read()\n",
            "IO is not a lowered type in the IR",
        ),
        (
            "other.py",
            "from typing import IO\n\ndef read(handle: IO[int]) -> str:\n    return handle.read()\n",
            "IO is not a lowered type in the IR",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr
