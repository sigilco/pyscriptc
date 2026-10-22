"""Real-Python backlog R7 + R14: wider for-iterables and loop ``else``.

R7 widens ``for`` to str (chars), dict (keys in insertion order),
list[list[T]] and list[tuple] (read-only elements), bytes (ints), and file
lines on the open() handle. R14 lowers ``for...else`` / ``while...else`` —
the else block runs iff no ``break`` fired. Each probe diffs stdout against
the CPython oracle element-for-element.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, write_case


def test_r7_for_over_str(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "for_str.py",
        "\n".join(
            [
                "def run() -> int:",
                "    s: str = 'abc'",
                "    for c in s:",
                "        print(c)",
                "    for c in 'xy':",
                "        print(c)",
                "    for c in '':",
                "        print('never')",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_r7_for_over_dict(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "for_dict.py",
        "\n".join(
            [
                "def run() -> int:",
                "    d: dict[str, int] = {}",
                "    for k in d:",
                "        print('never')",
                "    d['z'] = 1",
                "    d['a'] = 2",
                "    d['m'] = 3",
                "    for k in d:",
                "        print(k)",
                "    del d['a']",
                "    for k in d:",
                "        print(k, d[k])",
                "    e: dict[int, str] = {9: 'x', 1: 'y'}",
                "    for k2 in e:",
                "        print(k2, e[k2])",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_r7_for_over_list_of_lists(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "for_ll.py",
        "\n".join(
            [
                "def run() -> int:",
                "    grid: list[list[int]] = [[1, 2], [3], []]",
                "    for row in grid:",
                "        print(len(row))",
                "        for x in row:",
                "            print(x)",
                "    for row in grid:",
                "        print(row[0] if len(row) > 0 else -1)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_r7_for_over_list_of_tuples(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "for_lt.py",
        "\n".join(
            [
                "from typing import Tuple",
                "",
                "def run() -> int:",
                "    pairs: list[Tuple[int, str]] = [(1, 'a'), (2, 'b'), (3, 'c')]",
                "    for t in pairs:",
                "        print(t[0], t[1])",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_r7_for_over_bytes(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "for_bytes.py",
        "\n".join(
            [
                "def run() -> int:",
                "    bs: bytes = b'AB\\x00z'",
                "    for b in bs:",
                "        print(b)",
                "    for b in b'':",
                "        print('never')",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_r7_for_over_file_lines(tmp_path: Path) -> None:
    note = tmp_path / "lines.txt"
    path = write_case(
        tmp_path,
        "for_lines.py",
        "\n".join(
            [
                "import io",
                "from typing import BinaryIO, TextIO",
                "",
                "def run() -> int:",
                f"    p: str = {str(note)!r}",
                "    w: TextIO = open(p, 'w')",
                "    w.write('a\\nbb\\nlast')",
                "    w.close()",
                "    f: TextIO = open(p, 'r')",
                "    for line in f:",
                "        print('[' + line + ']')",
                "    f.close()",
                "    g: TextIO = open(p, 'r')",
                "    print(g.readline())",
                "    print(g.readline())",
                "    g.close()",
                "    s: TextIO = io.StringIO('p\\nq')",
                "    for line in s:",
                "        print('[' + line + ']')",
                "    b: BinaryIO = io.BytesIO(b'x\\ny\\n')",
                "    for raw in b:",
                "        print(len(raw))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_r14_for_else(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "for_else.py",
        "\n".join(
            [
                "def run() -> int:",
                "    for i in range(3):",
                "        print(i)",
                "    else:",
                "        print('else1')",
                "    for i in range(5):",
                "        if i == 2:",
                "            break",
                "        print(i)",
                "    else:",
                "        print('never')",
                "    for i in range(0):",
                "        print('never')",
                "    else:",
                "        print('else3')",
                "    for i in range(3):",
                "        continue",
                "    else:",
                "        print('else4')",
                "    for i in range(2):",
                "        for j in range(1):",
                "            print(i, j)",
                "        else:",
                "            print('inner')",
                "    else:",
                "        print('outer')",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_r14_while_else(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "while_else.py",
        "\n".join(
            [
                "def run() -> int:",
                "    i: int = 0",
                "    while i < 3:",
                "        i = i + 1",
                "    else:",
                "        print('welse1')",
                "    i = 0",
                "    while i < 9:",
                "        if i == 1:",
                "            break",
                "        i = i + 1",
                "    else:",
                "        print('never')",
                "    i = 0",
                "    while i < 3:",
                "        i = i + 1",
                "        continue",
                "    else:",
                "        print('welse3')",
                "    while i > 100:",
                "        print('never')",
                "    else:",
                "        print('welse4')",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_r14_else_break_targets_outer_loop(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "else_break.py",
        "\n".join(
            [
                "def run() -> int:",
                "    for i in range(3):",
                "        for j in range(2):",
                "            print(i, j)",
                "        else:",
                "            if i == 1:",
                "                break",
                "    else:",
                "        print('never')",
                "    print('done')",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_r14_else_body_return_and_flags(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "else_mix.py",
        "\n".join(
            [
                "def find(xs: list[int], want: int) -> int:",
                "    for x in xs:",
                "        if x == want:",
                "            break",
                "    else:",
                "        return -1",
                "    return x",
                "",
                "def run() -> int:",
                "    print(find([1, 2, 3], 2))",
                "    print(find([1, 2, 3], 9))",
                "    print(find([], 1))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")
