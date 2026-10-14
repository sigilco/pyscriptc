"""Keywords, typing erasure, and the scripting slice of the roadmap."""

from __future__ import annotations

import errno
from pathlib import Path

from tests.test_gates import compile_run, run_cli, write_case


def test_typing_imports_are_erased(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "typed.py",
        "\n".join(
            [
                "from __future__ import annotations",
                "from typing import List",
                "import typing",
                "",
                "def main() -> int:",
                "    xs: List[str] = ['ab', 'c']",
                "    return len(xs)",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "2\n"


def test_typing_value_stays_a_fence(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "cast.py",
        "\n".join(
            [
                "import typing",
                "from typing import get_type_hints",
                "",
                "def main() -> int:",
                "    typing.cast(int, 1)",
                "    get_type_hints(main)",
                "    return 0",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "cast.ll"))
    assert got.returncode == 3, got.stderr
    assert got.stderr.count("typing name") == 2
    assert "optional" not in got.stderr


def test_optional_annotation_is_a_fence(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "opt.py",
        "from typing import Optional\n\ndef main(x: Optional[int]) -> int:\n    return 1\n",
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "opt.ll"))
    assert got.returncode == 3
    assert "optional types are not in the IR" in got.stderr
    assert "closed import table" not in got.stderr


def test_every_statement_fence_is_reported(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "both.py",
        "\n".join(
            [
                "def main() -> int:",
                "    break",
                "    xs: list[bool] = [True]",
                "    return 1",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "both.ll"))
    assert got.returncode == 3
    assert "break outside a loop" in got.stderr
    assert "list[bool]" in got.stderr


def test_keywords_defaults_and_timedelta(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "kw.py",
        "\n".join(
            [
                "import datetime",
                "",
                "def add(x: int = 1, y: int = 2) -> int:",
                "    return x + y",
                "",
                "def total() -> int:",
                "    return add() + add(y=10) + add(3)",
                "",
                "def seconds() -> float:",
                "    return datetime.timedelta(seconds=1).total_seconds()",
                "",
            ]
        ),
    )
    total = compile_run(tmp_path, path, "total")
    assert total.returncode == 0, total.stderr
    assert total.stdout == "19\n"
    seconds = compile_run(tmp_path, path, "seconds")
    assert seconds.returncode == 0, seconds.stderr
    assert seconds.stdout == "1\n"


def test_truthiness_and_same_type_boolop(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "boolop.py",
        "\n".join(
            [
                "def pick(a: str, b: str) -> str:",
                "    return a or b",
                "",
                "def flag(text: str) -> int:",
                "    if text:",
                "        return 1",
                "    return 0",
                "",
                "def die() -> str:",
                "    raise ValueError('no')",
                "    return ''",
                "",
                "def keep() -> str:",
                "    return 'a' or die()",
                "",
            ]
        ),
    )
    assert compile_run(tmp_path, path, "pick", '["", "b"]').stdout == "b\n"
    assert compile_run(tmp_path, path, "pick", '["a", "b"]').stdout == "a\n"
    assert compile_run(tmp_path, path, "flag", '[""]').stdout == "0\n"
    assert compile_run(tmp_path, path, "flag", '["x"]').stdout == "1\n"
    assert compile_run(tmp_path, path, "keep").stdout == "a\n"


def test_strings_slices_formats_and_comprehensions(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "text.py",
        "\n".join(
            [
                "def letter() -> str:",
                "    text: str = 'café'",
                "    return text[3]",
                "",
                "def encoded() -> int:",
                "    text: str = 'café'",
                "    return len(text.encode())",
                "",
                "def roundtrip() -> str:",
                "    text: str = 'café'",
                "    return text.encode().decode()",
                "",
                "def mid(text: str) -> str:",
                "    parts: list[str] = text.split(',')",
                "    return parts[1]",
                "",
                "def joined() -> str:",
                "    xs: list[str] = ['a', 'b']",
                "    return ','.join(xs)",
                "",
                "def tail(text: str) -> str:",
                "    return text[1:]",
                "",
                "def head(text: str) -> str:",
                "    return text[:-1]",
                "",
                "def greet(name: str, n: int) -> str:",
                "    return f'{name} {n}'",
                "",
                "def greet2(name: str, n: int) -> str:",
                "    return '%s %d' % (name, n)",
                "",
                "def bump() -> int:",
                "    xs: list[int] = [1, 2]",
                "    ys: list[int] = [x + 1 for x in xs]",
                "    return ys[0] + ys[1]",
                "",
                "def show(n: int) -> str:",
                "    return str(n)",
                "",
                "def showf() -> str:",
                "    value: float = 1.5",
                "    return str(value)",
                "",
            ]
        ),
    )
    assert compile_run(tmp_path, path, "letter").stdout == "é\n"
    assert compile_run(tmp_path, path, "encoded").stdout == "5\n"
    assert compile_run(tmp_path, path, "roundtrip").stdout == "café\n"
    assert compile_run(tmp_path, path, "mid", '["a,b,c"]').stdout == "b\n"
    assert compile_run(tmp_path, path, "joined").stdout == "a,b\n"
    assert compile_run(tmp_path, path, "tail", '["abcd"]').stdout == "bcd\n"
    assert compile_run(tmp_path, path, "head", '["abcd"]').stdout == "abc\n"
    assert compile_run(tmp_path, path, "greet", '["ada", 3]').stdout == "ada 3\n"
    assert compile_run(tmp_path, path, "greet2", '["ada", 3]').stdout == "ada 3\n"
    assert compile_run(tmp_path, path, "bump").stdout == "5\n"
    assert compile_run(tmp_path, path, "show", "[-3]").stdout == "-3\n"
    assert compile_run(tmp_path, path, "showf").stdout == "1.5\n"


def test_module_constants_string_and_bisect(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "pkg.py",
        "\n".join(
            [
                "import bisect",
                "import errno",
                "import string",
                "",
                "VERSION = '2026.5'",
                "LIMIT: int = 3",
                "",
                "def version() -> str:",
                "    return VERSION",
                "",
                "def letters() -> int:",
                "    return len(string.ascii_letters)",
                "",
                "def missing() -> int:",
                "    return errno.ENOENT",
                "",
                "def place() -> int:",
                "    xs: list[int] = [1, 3, 5]",
                "    return bisect.bisect_left(xs, 3)",
                "",
                "def capped(n: int = LIMIT) -> int:",
                "    return n",
                "",
            ]
        ),
    )
    assert compile_run(tmp_path, path, "version").stdout == "2026.5\n"
    assert compile_run(tmp_path, path, "letters").stdout == "52\n"
    assert compile_run(tmp_path, path, "missing").stdout == f"{errno.ENOENT}\n"
    assert compile_run(tmp_path, path, "place").stdout == "1\n"
    assert compile_run(tmp_path, path, "capped").stdout == "3\n"


def test_with_open_closes_on_the_way_out(tmp_path: Path) -> None:
    source = write_case(
        tmp_path,
        "reader.py",
        "\n".join(
            [
                "def read(path: str) -> str:",
                "    with open(path) as handle:",
                "        return handle.read()",
                "",
                "def boom(path: str) -> int:",
                "    with open(path, 'w') as handle:",
                "        handle.write('no')",
                "        raise ValueError('no')",
                "    return 1",
                "",
            ]
        ),
    )
    data = tmp_path / "note.txt"
    data.write_text("hello", encoding="utf-8")
    got = compile_run(tmp_path, source, "read", repr([str(data)]))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "hello\n"
    out = tmp_path / "boom.txt"
    failed = compile_run(tmp_path, source, "boom", repr([str(out)]))
    assert failed.returncode == 1
    assert "ValueError: no" in failed.stderr
    assert out.read_text(encoding="utf-8") == "no"


def test_tuples_and_grouped_findall(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "pairs.py",
        "\n".join(
            [
                "import re",
                "from typing import Tuple",
                "",
                "def first() -> int:",
                "    item: tuple[int, str] = (1, 'ab')",
                "    return item[0] + len(item[-1])",
                "",
                "def groups() -> str:",
                "    found: list[Tuple[str, str]] = re.findall(r'(\\d+)-(\\w+)', '3-id 9-zz')",
                "    pair: Tuple[str, str] = found[0]",
                "    return pair[0] + pair[1]",
                "",
            ]
        ),
    )
    assert compile_run(tmp_path, path, "first").stdout == "3\n"
    assert compile_run(tmp_path, path, "groups").stdout == "3id\n"


def test_tuples_longer_than_four_fields(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "wide.py",
        "\n".join(
            [
                "names = ('a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i')",
                "",
                "def last() -> str:",
                "    row: tuple[int, int, int, int, int] = (1, 2, 3, 4, 9)",
                "    return names[8] + str(row[4])",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "last")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "i9\n"
    same = write_case(
        tmp_path,
        "eq.py",
        "def same() -> int:\n    if (1, 2) == (1, 2):\n        return 1\n    return 0\n",
    )
    refused = run_cli("compile", str(same), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "eq.ir.json"))
    assert refused.returncode == 3
    assert "comparison" in refused.stderr


def test_all_any_enumerate_and_zip_match(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "pred.py",
        "\n".join(
            [
                "def flags() -> int:",
                "    xs: list[int] = [1, 0, 2]",
                "    ys: list[int] = [5]",
                "    total: int = 0",
                "    if all(xs):",
                "        total = total + 1",
                "    if any(xs):",
                "        total = total + 2",
                "    empty: list[int] = []",
                "    if all(empty):",
                "        total = total + 4",
                "    for pair in enumerate(xs):",
                "        total = total + pair[0]",
                "    for item in zip(xs, ys):",
                "        total = total + item[0] + item[1]",
                "    return total",
                "",
            ]
        ),
    )
    # all([1,0,2]) is false, any is true, all([]) is true, enumerate adds 0+1+2, zip adds 1+5.
    got = compile_run(tmp_path, path, "flags")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "15\n"


def test_version_check_and_global_slot(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "slot.py",
        "\n".join(
            [
                "import sys",
                "",
                "label = None",
                "",
                "if sys.version_info >= (3, 11):",
                "    def pick() -> int:",
                "        return 11",
                "else:",
                "    def pick() -> int:",
                "        return 0",
                "",
                "def greet() -> str:",
                "    global label",
                "    if label is None:",
                "        label = 'ok'",
                "    return label",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "pick")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "11\n"
    greeted = compile_run(tmp_path, path, "greet")
    assert greeted.returncode == 0, greeted.stderr
    assert greeted.stdout == "ok\n"
