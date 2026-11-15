# SPDX-License-Identifier: Apache-2.0
"""`read(n)`: the bounded read over a file handle and a memory handle.

CPython's `read(n)` reads at most `n` items from the current position: a
text handle counts code points, a binary handle counts bytes, and a
memory handle (BytesIO, StringIO) counts the same way as its own kind.
The read stops short at EOF and returns an empty buffer there, and the
position ends where the read ended, so a later `read`, `read(n)` or
`readline` continues after it.

The probes run the same program on both sides: the compiled binary and
the pinned CPython 3.14, byte for byte.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case


def _oracle(tmp_path: Path, name: str, source: str, func: str = "run") -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func)


def test_read_n_bounds_the_read_and_stops_at_eof(tmp_path: Path) -> None:
    data = repr(str(tmp_path / "data.txt"))
    _oracle(
        tmp_path,
        "read_n.py",
        "\n".join(
            [
                "def run() -> int:",
                f"    with open({data}, 'w') as out:",
                "        out.write('hello world')",
                f"    with open({data}, 'r') as handle:",
                "        print(repr(handle.read(5)))",
                "        print(repr(handle.read(5)))",
                "        print(repr(handle.read(5)))",
                "        print(repr(handle.read(5)))",
                "        print(repr(handle.read(1)))",
                "    return 0",
                "",
            ]
        ),
    )


def test_read_n_binary_counts_bytes(tmp_path: Path) -> None:
    data = repr(str(tmp_path / "data.bin"))
    _oracle(
        tmp_path,
        "read_n_bin.py",
        "\n".join(
            [
                "def run() -> int:",
                f"    with open({data}, 'wb') as out:",
                "        out.write(b'\\x00\\x01\\x02\\xff\\xfe')",
                f"    with open({data}, 'rb') as handle:",
                "        print(repr(handle.read(3)))",
                "        print(repr(handle.read(2)))",
                "        print(repr(handle.read(2)))",
                "        print(repr(handle.read(1)))",
                "    return 0",
                "",
            ]
        ),
    )


def test_read_n_text_counts_code_points(tmp_path: Path) -> None:
    data = repr(str(tmp_path / "data.txt"))
    _oracle(
        tmp_path,
        "read_n_text.py",
        "\n".join(
            [
                "def run() -> int:",
                f"    with open({data}, 'w') as out:",
                "        out.write('héllo wörld')",
                f"    with open({data}, 'r') as handle:",
                "        print(repr(handle.read(3)))",
                "        print(repr(handle.read(2)))",
                "        print(repr(handle.read(99)))",
                "    return 0",
                "",
            ]
        ),
    )


def test_read_n_text_keeps_the_code_point_whole(tmp_path: Path) -> None:
    # A code point is never split: the budget counts the characters, and
    # the bytes of the last one ride out with it.
    data = repr(str(tmp_path / "data.txt"))
    _oracle(
        tmp_path,
        "read_n_whole.py",
        "\n".join(
            [
                "def run() -> int:",
                f"    with open({data}, 'w') as out:",
                "        out.write('éèê')",
                f"    with open({data}, 'r') as handle:",
                "        print(repr(handle.read(1)))",
                "        print(repr(handle.read(1)))",
                "        print(repr(handle.read(1)))",
                "        print(repr(handle.read(1)))",
                "    return 0",
                "",
            ]
        ),
    )


def test_read_n_keeps_the_file_position(tmp_path: Path) -> None:
    data = repr(str(tmp_path / "data.txt"))
    _oracle(
        tmp_path,
        "read_n_pos.py",
        "\n".join(
            [
                "def run() -> int:",
                f"    with open({data}, 'w') as out:",
                "        out.write('line1\\nline2\\nline3\\n')",
                f"    with open({data}, 'r') as handle:",
                "        print(repr(handle.readline()))",
                "        print(repr(handle.read(5)))",
                "        print(repr(handle.readline()))",
                "        print(repr(handle.read(99)))",
                f"    with open({data}, 'rb') as handle:",
                "        print(repr(handle.read(4)))",
                "        print(repr(handle.readline()))",
                "        print(repr(handle.read(3)))",
                "    return 0",
                "",
            ]
        ),
    )


def test_read_n_zero_and_negative(tmp_path: Path) -> None:
    data = repr(str(tmp_path / "data.txt"))
    _oracle(
        tmp_path,
        "read_n_edge.py",
        "\n".join(
            [
                "def run() -> int:",
                f"    with open({data}, 'w') as out:",
                "        out.write('abc')",
                f"    with open({data}, 'r') as handle:",
                "        print(repr(handle.read(0)))",
                "        print(repr(handle.read(-1)))",
                "        print(repr(handle.read(1)))",
                "    return 0",
                "",
            ]
        ),
    )


def test_read_n_over_memory_handles(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "read_n_mem.py",
        "\n".join(
            [
                "import io",
                "",
                "def run() -> int:",
                "    buf: io.BytesIO = io.BytesIO(b'abcdef')",
                "    print(repr(buf.read(2)))",
                "    print(repr(buf.read(10)))",
                "    print(repr(buf.read(1)))",
                "    mem: io.StringIO = io.StringIO('abcdef')",
                "    print(repr(mem.read(2)))",
                "    print(repr(mem.read(10)))",
                "    print(repr(mem.read(1)))",
                "    return 0",
                "",
            ]
        ),
    )


def test_read_n_over_a_typed_handle(tmp_path: Path) -> None:
    data = repr(str(tmp_path / "data.txt"))
    _oracle(
        tmp_path,
        "read_n_typed.py",
        "\n".join(
            [
                "from typing import BinaryIO, TextIO",
                "",
                "def head(handle: TextIO) -> str:",
                "    return handle.read(4)",
                "",
                "def raw(handle: BinaryIO) -> bytes:",
                "    return handle.read(2)",
                "",
                "def run() -> int:",
                f"    with open({data}, 'w') as out:",
                "        out.write('hello world')",
                f"    with open({data}, 'r') as handle:",
                "        print(repr(head(handle)))",
                f"    with open({data}, 'rb') as handle:",
                "        print(repr(raw(handle)))",
                "    return 0",
                "",
            ]
        ),
    )


def test_read_n_fences_the_other_forms(tmp_path: Path) -> None:
    # CPython's `read` takes no keyword argument, and one size is the
    # whole shape: a second argument and a keyword both stop the compile.
    from pyscriptc.lower import lower_source

    for source, message in (
        ("with open('a', 'r') as h:\n    s: str = h.read(1, 2)\n", "read expects 0 or 1 argument(s)"),
        ("with open('a', 'r') as h:\n    s: str = h.read(size=1)\n", "keyword arguments are not lowered"),
    ):
        mod = lower_source(source)
        fences = mod["fences"]
        assert fences, source
        assert fences[0]["code"] == "PS2003", fences
        assert message in fences[0]["message"], fences
