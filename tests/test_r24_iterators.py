"""R24 iterator protocol: iter/next builtins, map/filter/reversed as values,
range/enumerate/zip as stored values, and generator expressions.

Model: iterator values are cursor records ``{seq: list[T], idx: int}`` —
the sequence materializes eagerly (lists stay lists; dicts materialize their
keys; str/bytes/file iterators materialize into lists first), and ``next`` /
``for`` / ``list`` advance or drain the cursor. Generator expressions are the
same eager model: ``(x*2 for x in xs)`` materializes into a list at definition.

Each oracle test compiles a probe and diffs its stdout against CPython.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import oracle, run_cli, write_case


def _oracle_diff(tmp_path: Path, name: str, source: str, func: str = "probe") -> None:
    path = write_case(tmp_path, name, source)
    out = str(tmp_path / name.removesuffix(".py"))
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", func, "-o", out)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func)


def test_iter_next_and_default(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r24_iternext.py",
        "\n".join(
            [
                "from typing import Iterator",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [10, 20, 30]",
                "    it: Iterator[int] = iter(xs)",
                "    a: int = next(it)",
                "    b: int = next(it)",
                "    c: int = next(it)",
                "    d: int = next(it, 99)",
                "    e: int = next(it, 7)",
                "    return a + b * 10 + c * 100 + d * 1000 + e * 10000",
                "",
            ]
        ),
    )


def test_for_loop_consumes_iterator(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r24_fordrain.py",
        "\n".join(
            [
                "from typing import Iterator",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1, 2, 3, 4]",
                "    it: Iterator[int] = iter(xs)",
                "    total: int = 0",
                "    for x in it:",
                "        total += x",
                "    for x in it:",
                "        total += 100",
                "    return total + next(it, 0)",
                "",
            ]
        ),
    )


def test_iter_of_iterator_is_same_cursor(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r24_iteriter.py",
        "\n".join(
            [
                "from typing import Iterator",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [5, 6, 7]",
                "    it: Iterator[int] = iter(xs)",
                "    a: int = next(it)",
                "    it2: Iterator[int] = iter(it)",
                "    b: int = next(it2)",
                "    c: int = next(it)",
                "    return a * 100 + b * 10 + c",
                "",
            ]
        ),
    )


def test_list_drains_iterator(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r24_listdrain.py",
        "\n".join(
            [
                "from typing import Iterator",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1, 2, 3, 4, 5]",
                "    it: Iterator[int] = iter(xs)",
                "    skip: int = next(it)",
                "    rest: list[int] = list(it)",
                "    again: list[int] = list(it)",
                "    return skip * 100 + len(rest) * 10 + len(again)",
                "",
            ]
        ),
    )


def test_map_and_filter_as_values(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r24_mapfilter.py",
        "\n".join(
            [
                "from typing import Iterator",
                "",
                "def double(x: int) -> int:",
                "    return x * 2",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1, 2, 3, 4]",
                "    ys: Iterator[int] = map(double, xs)",
                "    doubled: list[int] = list(ys)",
                "    zs: Iterator[int] = filter(None, doubled)",
                "    total: int = 0",
                "    for v in map(double, zs):",
                "        total += v",
                "    return doubled[1] * 100 + total",
                "",
            ]
        ),
    )


def test_map_lambda_and_two_iterables(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r24_maplambda.py",
        "\n".join(
            [
                "from typing import Iterator",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1, 2, 3]",
                "    ys: Iterator[int] = map(lambda v: v + 10, xs)",
                "    total: int = 0",
                "    for v in ys:",
                "        total += v",
                "    zs: list[int] = list(map(lambda a, b: a * b, xs, xs))",
                "    return total * 100 + zs[2]",
                "",
            ]
        ),
    )


def test_range_enumerate_zip_as_values(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r24_values.py",
        "\n".join(
            [
                "from typing import Iterator",
                "",
                "def probe() -> int:",
                "    r: list[int] = range(2, 8, 2)",
                "    e: Iterator[tuple[int, int]] = enumerate(r, 5)",
                "    total: int = 0",
                "    for i, v in e:",
                "        total += i * 10 + v",
                "    zs: Iterator[tuple[int, int]] = zip(r, r)",
                "    prod: int = 0",
                "    for a, b in zs:",
                "        prod += a * b",
                "    return len(r) * 100 + total + prod",
                "",
            ]
        ),
    )


def test_reversed_as_value(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r24_reversed.py",
        "\n".join(
            [
                "from typing import Iterator",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1, 2, 3]",
                "    rs: Iterator[int] = reversed(xs)",
                "    total: int = 0",
                "    for v in rs:",
                "        total += v",
                "    back: list[int] = list(reversed(xs))",
                "    return back[0] * 100 + total",
                "",
            ]
        ),
    )


def test_genexpr_materializes(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r24_genexpr.py",
        "\n".join(
            [
                "from typing import Iterable",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1, 2, 3, 4]",
                "    g: Iterable[int] = (x * 2 for x in xs)",
                "    total: int = 0",
                "    for v in g:",
                "        total += v",
                "    s: int = sum(x * x for x in xs)",
                "    return total * 100 + s",
                "",
            ]
        ),
    )


def test_stop_iteration_raises_and_catches(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r24_stopiter.py",
        "\n".join(
            [
                "from typing import Iterator",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1]",
                "    it: Iterator[int] = iter(xs)",
                "    a: int = next(it)",
                "    total: int = a",
                "    try:",
                "        b: int = next(it)",
                "        total += b",
                "    except StopIteration:",
                "        total += 1000",
                "    try:",
                "        c: int = next(it)",
                "        total += c",
                "    except Exception:",
                "        total += 10000",
                "    return total",
                "",
            ]
        ),
    )


def test_iter_of_dict_keys_and_str(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r24_dictkeys.py",
        "\n".join(
            [
                "from typing import Iterator",
                "",
                "def probe() -> int:",
                "    d: dict[str, int] = {'a': 1, 'b': 2}",
                "    n: int = 0",
                "    for k in iter(d):",
                "        n += d[k]",
                "    m: int = 0",
                "    for c in iter('ab'):",
                "        m += 10",
                "    return n * 10 + m",
                "",
            ]
        ),
    )


def test_iter_fences_on_non_sequences(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "r24_baditer.py",
        "def probe() -> int:\n    it = iter(5)\n    return 0\n",
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "bad.ll"))
    assert got.returncode == 3, got.stderr


def test_next_fences_on_plain_lists(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "r24_badnext.py",
        "def probe() -> int:\n    xs: list[int] = [1]\n    return next(xs)\n",
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "bad2.ll"))
    assert got.returncode == 3, got.stderr
