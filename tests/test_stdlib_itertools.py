"""itertools: the script-shaped surface.

`filterfalse`, `chain`, `chain.from_iterable` and `starmap` join the
lowered set. Each probe diffs stdout against the CPython oracle.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case


def _oracle(tmp_path: Path, name: str, source: str, func: str = "run") -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func), got.stdout


def test_filterfalse(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "filterfalse.py",
        "\n".join(
            [
                "import itertools",
                "",
                "def run() -> int:",
                "    xs: list[int] = [1, 2, 3, 4, 5, 6]",
                "    for x in itertools.filterfalse(lambda v: v % 2 == 0, xs):",
                "        print(x)",
                "    return 0",
                "",
                "def words() -> int:",
                "    ws: list[str] = ['a', 'bb', 'c']",
                "    for w in itertools.filterfalse(lambda v: len(v) == 2, ws):",
                "        print(w)",
                "    return 0",
                "",
            ]
        ),
        "run",
    )
    _oracle(
        tmp_path,
        "filterfalse_words.py",
        "\n".join(
            [
                "import itertools",
                "",
                "def run() -> int:",
                "    ws: list[str] = ['a', 'bb', 'c']",
                "    for w in itertools.filterfalse(lambda v: len(v) == 2, ws):",
                "        print(w)",
                "    return 0",
                "",
            ]
        ),
    )


def test_chain(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "chain.py",
        "\n".join(
            [
                "import itertools",
                "",
                "def run() -> int:",
                "    a: list[int] = [1, 2]",
                "    b: list[int] = [3, 4]",
                "    for x in itertools.chain(a, b):",
                "        print(x)",
                "    c: list[int] = [5]",
                "    for y in itertools.chain(a, c):",
                "        print(y)",
                "    return 0",
                "",
            ]
        ),
    )


def test_chain_from_iterable(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "chain_from_iterable.py",
        "\n".join(
            [
                "import itertools",
                "",
                "def run() -> int:",
                "    groups: list[list[int]] = [[1, 2], [], [3]]",
                "    for x in itertools.chain.from_iterable(groups):",
                "        print(x)",
                "    return 0",
                "",
            ]
        ),
    )


def test_starmap(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "starmap.py",
        "\n".join(
            [
                "import itertools",
                "",
                "def add(a: int, b: int) -> int:",
                "    return a + b",
                "",
                "def run() -> int:",
                "    pairs: list[tuple[int, int]] = [(1, 2), (3, 4)]",
                "    for x in itertools.starmap(add, pairs):",
                "        print(x)",
                "    for y in itertools.starmap(lambda a, b: a * b, pairs):",
                "        print(y)",
                "    return 0",
                "",
            ]
        ),
    )
