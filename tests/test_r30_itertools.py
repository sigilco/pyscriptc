"""R30 itertools rest: islice/product/combinations/accumulate/zip_longest/
takewhile/groupby lower to eager iterator records (the R24 model — a cursor
``{seq: list[T], idx: int}`` over a materialized list), so ``for``, ``next``,
and ``list`` drain them like ``map``/``zip``.

- ``islice``/``takewhile`` consume from an iterator argument's cursor exactly
  like CPython's lazy pull (``islice`` reads ``stop`` elements; ``takewhile``
  reads through the first failure).
- ``zip_longest`` fields become the tagged ``T | None`` when ``fillvalue`` is
  omitted or ``None``; an explicit fillvalue must match every element type.
- ``groupby`` yields ``(key, group)`` pairs where the group is a materialized
  list standing in for CPython's grouper iterator.
- ``count``/``cycle`` stay fenced — an unbounded iterator cannot materialize;
  ``repeat(x, n)`` covers the bounded spelling.

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


def _fence(tmp_path: Path, name: str, source: str) -> str:
    path = write_case(tmp_path, name, source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "bad.ll"))
    assert got.returncode == 3, got.stderr
    return got.stderr


def test_islice_for_and_list(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r30_islice.py",
        "\n".join(
            [
                "import itertools",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1, 2, 3, 4, 5]",
                "    total: int = 0",
                "    for x in itertools.islice(xs, 2):",
                "        total += x",
                "    ys: list[int] = list(itertools.islice(xs, 1, 4))",
                "    zs: list[int] = list(itertools.islice(xs, 0, 5, 2))",
                "    return total * 100 + len(ys) * 10 + ys[2] + len(zs) * 1000 + zs[2]",
                "",
            ]
        ),
    )


def test_islice_consumes_iterator_cursor(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r30_isliceit.py",
        "\n".join(
            [
                "import itertools",
                "from typing import Iterator",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1, 2, 3, 4, 5]",
                "    it: Iterator[int] = iter(xs)",
                "    total: int = 0",
                "    for x in itertools.islice(it, 2):",
                "        total += x",
                "    total += next(it) * 100",
                "    return total",
                "",
            ]
        ),
    )


def test_islice_over_str_and_iterators(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r30_islicestr.py",
        "\n".join(
            [
                "import itertools",
                "",
                "def probe() -> int:",
                "    total: int = 0",
                "    for ch in itertools.islice('abcdef', 3):",
                "        total += len(ch)",
                "    for ch in itertools.islice('abcdef', 1, 6, 2):",
                "        total += len(ch) * 10",
                "    return total",
                "",
            ]
        ),
    )


def test_islice_bad_bounds_raise(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "r30_isliceneg.py",
        "\n".join(
            [
                "import itertools",
                "",
                "def probe() -> int:",
                "    for x in itertools.islice([1, 2, 3], -1):",
                "        return x",
                "    return 0",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "probe", "-o", str(tmp_path / "neg"))
    assert got.returncode == 1
    assert "ValueError" in got.stderr


def test_product_two_and_repeat(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r30_product.py",
        "\n".join(
            [
                "import itertools",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1, 2, 3]",
                "    ys: list[int] = [4, 5]",
                "    total: int = 0",
                "    for a, b in itertools.product(xs, ys):",
                "        total += a * b",
                "    for a, b in itertools.product(xs, repeat=2):",
                "        total += a - b",
                "    for c, d in itertools.product('ab', 'xy'):",
                "        total += len(c + d)",
                "    return total",
                "",
            ]
        ),
    )


def test_combinations(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r30_combinations.py",
        "\n".join(
            [
                "import itertools",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1, 2, 3, 4]",
                "    total: int = 0",
                "    for pair in itertools.combinations(xs, 2):",
                "        total += pair[0] * pair[1]",
                "    for trio in itertools.combinations(xs, 3):",
                "        total += trio[0] + trio[1] + trio[2]",
                "    for tail in itertools.combinations(xs, 9):",
                "        total += tail[0]",
                "    return total",
                "",
            ]
        ),
    )


def test_accumulate(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r30_accumulate.py",
        "\n".join(
            [
                "import itertools",
                "import operator",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1, 2, 3, 4]",
                "    total: int = 0",
                "    for x in itertools.accumulate(xs):",
                "        total += x",
                "    for x in itertools.accumulate(xs, operator.mul):",
                "        total += x",
                "    for x in itertools.accumulate(xs, lambda a, b: a - b):",
                "        total += x",
                "    return total",
                "",
            ]
        ),
    )


def test_zip_longest_fillvalue(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r30_ziplongest.py",
        "\n".join(
            [
                "import itertools",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1, 2]",
                "    ys: list[int] = [10, 20, 30]",
                "    total: int = 0",
                "    for a, b in itertools.zip_longest(xs, ys, fillvalue=7):",
                "        total += a * 100 + b",
                "    return total",
                "",
            ]
        ),
    )


def test_zip_longest_none_fill(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r30_ziplongestnone.py",
        "\n".join(
            [
                "import itertools",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1, 2]",
                "    ys: list[int] = [10, 20, 30]",
                "    total: int = 0",
                "    for a, b in itertools.zip_longest(xs, ys):",
                "        if a is None:",
                "            total += 1",
                "        else:",
                "            total += a * 100",
                "        if b is None:",
                "            total += 5",
                "        else:",
                "            total += b",
                "    return total",
                "",
            ]
        ),
    )


def test_takewhile(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r30_takewhile.py",
        "\n".join(
            [
                "import itertools",
                "",
                "def small(x: int) -> bool:",
                "    return x < 3",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1, 2, 3, 1, 1]",
                "    total: int = 0",
                "    for x in itertools.takewhile(small, xs):",
                "        total += x",
                "    for x in itertools.takewhile(lambda x: x <= 2, xs):",
                "        total += x * 10",
                "    return total",
                "",
            ]
        ),
    )


def test_takewhile_consumes_iterator_cursor(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r30_takewhileit.py",
        "\n".join(
            [
                "import itertools",
                "from typing import Iterator",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1, 2, 3, 4, 5]",
                "    it: Iterator[int] = iter(xs)",
                "    total: int = 0",
                "    for x in itertools.islice(it, 2):",
                "        total += x",
                "    for x in itertools.takewhile(lambda v: v < 4, it):",
                "        total += x * 10",
                "    total += next(it) * 1000",
                "    return total",
                "",
            ]
        ),
    )


def test_groupby(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r30_groupby.py",
        "\n".join(
            [
                "import itertools",
                "",
                "def key(x: int) -> int:",
                "    return x % 3",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1, 2, 3, 4, 5, 6]",
                "    total: int = 0",
                "    for k1, g1 in itertools.groupby(xs):",
                "        for x in g1:",
                "            total += k1 * 100 + x",
                "    for k2, g2 in itertools.groupby(xs, key):",
                "        for x in g2:",
                "            total += k2 * 10 + x",
                "    for k3, g3 in itertools.groupby(xs, lambda x: x > 3):",
                "        for x in g3:",
                "            total += x",
                "        if k3:",
                "            total += 1000",
                "    return total",
                "",
            ]
        ),
    )


def test_iterator_values_bind_and_drain(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r30_bindings.py",
        "\n".join(
            [
                "import itertools",
                "from typing import Iterator",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1, 2, 3, 4, 5]",
                "    it: Iterator[int] = itertools.islice(xs, 3)",
                "    a: int = next(it)",
                "    rest: list[int] = list(it)",
                "    zs: list[int] = list(itertools.accumulate(xs))",
                "    return a * 100 + len(rest) + rest[0] * 10 + zs[4]",
                "",
            ]
        ),
    )


def test_count_and_cycle_stay_fenced(tmp_path: Path) -> None:
    stderr = _fence(
        tmp_path,
        "r30_count.py",
        "\n".join(
            [
                "import itertools",
                "from typing import Iterator",
                "",
                "def probe() -> int:",
                "    it: Iterator[int] = itertools.count(0)",
                "    return 0",
            ]
        ),
    )
    assert "infinite iterators are not lowered" in stderr
    stderr = _fence(
        tmp_path,
        "r30_cycle.py",
        "\n".join(
            [
                "import itertools",
                "",
                "def probe() -> int:",
                "    total: int = 0",
                "    for x in itertools.cycle([1, 2]):",
                "        total += x",
                "    return total",
            ]
        ),
    )
    assert "infinite iterators are not lowered" in stderr


def test_unlowered_itertools_fences(tmp_path: Path) -> None:
    stderr = _fence(
        tmp_path,
        "r30_batched.py",
        "\n".join(
            [
                "import itertools",
                "",
                "def probe() -> int:",
                "    total: int = 0",
                "    for x in itertools.batched([1, 2, 3], 2):",
                "        total += 1",
                "    return total",
            ]
        ),
    )
    assert "itertools.batched is not in the lowered subset of itertools" in stderr
    stderr = _fence(
        tmp_path,
        "r30_starmap_builtin.py",
        "\n".join(
            [
                "import itertools",
                "",
                "def probe() -> int:",
                "    total: int = 0",
                "    for x in itertools.starmap(pow, [(2, 3)]):",
                "        total += x",
                "    return total",
            ]
        ),
    )
    assert "itertools.starmap needs a callable of known arity" in stderr
    stderr = _fence(
        tmp_path,
        "r30_takewhile_arity.py",
        "\n".join(
            [
                "import itertools",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1]",
                "    return len(list(itertools.takewhile(xs)))",
            ]
        ),
    )
    assert "itertools.takewhile takes a predicate and an iterable" in stderr
    stderr = _fence(
        tmp_path,
        "r30_combinations_r.py",
        "\n".join(
            [
                "import itertools",
                "",
                "def probe() -> int:",
                "    xs: list[int] = [1, 2]",
                "    r: int = 2",
                "    return len(list(itertools.combinations(xs, r)))",
            ]
        ),
    )
    assert "itertools.combinations count is a literal int of 2 or more" in stderr
