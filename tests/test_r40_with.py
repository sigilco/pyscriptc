"""R40 `with` generality: multi-item `with` and file-class receivers.

`with a, b:` lowers as `with a: with b:` — each item keeps its single-item
machinery, and the nested shape closes handles inside-out on the normal,
raised, and early-return paths. A TextIO/BytesIO receiver (an `open()` result
bound to a name, `io.StringIO`, `io.BytesIO`, a `NamedTemporaryFile` handle)
is its own context manager: `as` rebinds the handle and both exits close it.

Each oracle test compiles a probe and diffs its stdout against CPython.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import oracle, run_cli, write_case


def _oracle_diff(tmp_path: Path, name: str, source: str, func: str = "probe", args: str = "[]") -> None:
    path = write_case(tmp_path, name, source)
    out = str(tmp_path / name.removesuffix(".py"))
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", func, "--args", args, "-o", out)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func, args)


def test_multi_item_open(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("alpha\n", encoding="utf-8")
    b.write_text("beta\n", encoding="utf-8")
    _oracle_diff(
        tmp_path,
        "r40_multi.py",
        "\n".join(
            [
                "def probe(a: str, b: str) -> int:",
                "    with open(a) as f, open(b) as g:",
                "        print(f.read())",
                "        print(g.read())",
                "    try:",
                "        f.read()",
                "    except ValueError:",
                '        print("f closed")',
                "    try:",
                "        g.read()",
                "    except ValueError:",
                '        print("g closed")',
                "    return 0",
                "",
            ]
        ),
        args=repr([str(a), str(b)]),
    )


def test_multi_item_three_files(tmp_path: Path) -> None:
    paths = []
    for name in ("x", "y", "z"):
        path = tmp_path / f"{name}.txt"
        path.write_text(f"{name}-line\n", encoding="utf-8")
        paths.append(str(path))
    _oracle_diff(
        tmp_path,
        "r40_three.py",
        "\n".join(
            [
                "def probe(a: str, b: str, c: str) -> int:",
                "    with open(a) as f, open(b) as g, open(c) as h:",
                "        print(f.read() + g.read() + h.read())",
                "    return 0",
                "",
            ]
        ),
        args=repr(paths),
    )


def test_multi_item_exception_closes_all(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("alpha\n", encoding="utf-8")
    b.write_text("beta\n", encoding="utf-8")
    _oracle_diff(
        tmp_path,
        "r40_raise.py",
        "\n".join(
            [
                "def probe(a: str, b: str) -> int:",
                "    try:",
                "        with open(a) as f, open(b) as g:",
                '            raise ValueError("mid")',
                "    except ValueError:",
                '        print("caught")',
                "    try:",
                "        f.read()",
                "    except ValueError:",
                '        print("f closed")',
                "    try:",
                "        g.read()",
                "    except ValueError:",
                '        print("g closed")',
                "    return 0",
                "",
            ]
        ),
        args=repr([str(a), str(b)]),
    )


def test_multi_item_mixed_managers(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    a.write_text("alpha\n", encoding="utf-8")
    _oracle_diff(
        tmp_path,
        "r40_mixed.py",
        "\n".join(
            [
                "from contextlib import suppress",
                "",
                "class CM:",
                "    tag: str",
                "    def __init__(self, tag: str) -> None:",
                "        self.tag = tag",
                "    def __enter__(self) -> str:",
                '        print("enter " + self.tag)',
                '        return "bound-" + self.tag',
                "    def __exit__(self, et: str | None, ev: Exception | None, tb: str | None) -> bool:"
                "  # type: ignore[exit-return]",
                '        print("exit " + self.tag)',
                "        return False",
                "",
                "def probe(a: str) -> int:",
                '    with CM("cm") as cm, open(a) as f:',
                "        print(cm)",
                "        print(f.read())",
                "    with suppress(ValueError), open(a) as f2:",
                '        raise ValueError("swallowed")',
                "    try:",
                "        f2.read()",
                "    except ValueError:",
                '        print("suppressed file closed")',
                "    return 0",
                "",
            ]
        ),
        args=repr([str(a)]),
    )


def test_with_file_receiver(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    a.write_text("alpha\n", encoding="utf-8")
    _oracle_diff(
        tmp_path,
        "r40_receiver.py",
        "\n".join(
            [
                "import io",
                "from typing import TextIO",
                "",
                "def probe(a: str) -> int:",
                "    f: TextIO = open(a)",
                "    with f:",
                "        print(f.read())",
                "    try:",
                "        f.read()",
                "    except ValueError:",
                '        print("f closed")',
                "    g: TextIO = open(a)",
                "    with g as h:",
                "        print(h.read())",
                "    try:",
                "        h.read()",
                "    except ValueError:",
                '        print("h closed")',
                '    with io.StringIO("sio") as s:',
                "        print(s.read())",
                "    return 0",
                "",
            ]
        ),
        args=repr([str(a)]),
    )


def test_with_binary_receiver(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r40_binary.py",
        "\n".join(
            [
                "import io",
                "from typing import BinaryIO",
                "",
                "def probe() -> int:",
                '    b: BinaryIO = io.BytesIO(b"bio")',
                "    with b:",
                "        print(b.read())",
                '    with io.BytesIO(b"two") as b2:',
                "        print(b2.read())",
                "    return 0",
                "",
            ]
        ),
    )


def test_with_named_temp_receiver(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r40_ntf.py",
        "\n".join(
            [
                "import tempfile",
                "from typing import TextIO",
                "",
                "def probe() -> int:",
                '    fh: TextIO = tempfile.NamedTemporaryFile(mode="w+")  # type: ignore[assignment]',
                "    with fh:",
                '        fh.write("hi")',
                "    try:",
                "        fh.read()",
                "    except ValueError:",
                '        print("ntf closed")',
                "    return 0",
                "",
            ]
        ),
    )


def test_with_fences(tmp_path: Path) -> None:
    cases = [
        (
            "noas.py",
            "def f() -> int:\n    with open('/tmp/x'):\n        return 1\n    return 0\n",
            "PS2002",
            "with open needs a name",
        ),
        (
            "noas_inner.py",
            "\n".join(
                [
                    "def f() -> int:",
                    "    with open('/tmp/x') as fh, open('/tmp/y'):",
                    "        return 1",
                    "    return 0",
                    "",
                ]
            ),
            "PS2002",
            "with open needs a name",
        ),
        (
            "tuple_target.py",
            "\n".join(
                [
                    "from typing import TextIO",
                    "def f() -> int:",
                    "    fh: TextIO = open('/tmp/x')",
                    "    with fh as (x, y):",
                    "        return 1",
                    "    return 0",
                    "",
                ]
            ),
            "PS2002",
            "with-as needs a name",
        ),
        (
            "suppress_as.py",
            "\n".join(
                [
                    "from contextlib import suppress",
                    "def f() -> int:",
                    "    with open('/tmp/x') as fh, suppress(ValueError) as e:",
                    "        return 1",
                    "    return 0",
                    "",
                ]
            ),
            "PS1206",
            "with-as",
        ),
    ]
    for name, source, code, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert code in got.stderr
        assert text in got.stderr
