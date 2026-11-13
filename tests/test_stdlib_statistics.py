"""statistics: the closed subset over `list[int]` and `list[float]`.

The deterministic rows diff against the CPython oracle byte for byte.
Two seams are recorded in divergence 64 (D64) instead of asserted equal:

- the mean/median/variance family over `list[int]` returns `float` where
  CPython returns `int` (equal value, different repr), and
- `stdev`/`pstdev` differ from CPython's correctly-rounded
  exact-rational square root by one ulp on a minority of datasets.
"""

from __future__ import annotations

import statistics as py_statistics
from pathlib import Path

from tests.test_gates import compile_run, write_case


def _run(tmp_path: Path, name: str, source: str) -> str:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    return got.stdout


def _program(body: list[str]) -> str:
    return "\n".join(
        ["import statistics", "", "def run() -> int:"] + [f"    {line}" for line in body] + ["    return 0", ""]
    )


def test_median_family_matches_cpython(tmp_path: Path) -> None:
    body = [
        "xs: list[int] = [4, 1, 3, 1, 5]",
        "ys: list[int] = [1, 2, 3, 4, 5, 6]",
        "zs: list[float] = [1.5, 2.5, 3.5, 4.5, 5.5]",
        "print(statistics.median_low(xs), statistics.median_high(xs))",
        "print(statistics.median_low(ys), statistics.median_high(ys))",
        "print(statistics.median_low(zs), statistics.median_high(zs))",
    ]
    expected = "\n".join(
        [
            f"{py_statistics.median_low([4, 1, 3, 1, 5])} {py_statistics.median_high([4, 1, 3, 1, 5])}",
            f"{py_statistics.median_low([1, 2, 3, 4, 5, 6])} {py_statistics.median_high([1, 2, 3, 4, 5, 6])}",
            f"{py_statistics.median_low([1.5, 2.5, 3.5, 4.5, 5.5])} "
            f"{py_statistics.median_high([1.5, 2.5, 3.5, 4.5, 5.5])}",
        ]
    )
    assert _run(tmp_path, "median.py", _program(body)) == expected + "\n0\n"


def test_mode_matches_cpython(tmp_path: Path) -> None:
    body = [
        "xs: list[int] = [4, 1, 3, 1, 5]",
        "ys: list[int] = [1, 2, 2, 3, 3, 3]",
        "print(statistics.mode(xs))",
        "print(statistics.mode(ys))",
    ]
    expected = "\n".join([str(py_statistics.mode([4, 1, 3, 1, 5])), str(py_statistics.mode([1, 2, 2, 3, 3, 3]))])
    assert _run(tmp_path, "mode.py", _program(body)) == expected + "\n0\n"


def test_variance_family_matches_cpython(tmp_path: Path) -> None:
    xs = [1, 2, 3, 4, 5, 6]
    body = [
        f"xs: list[int] = {xs}",
        "print(statistics.pvariance(xs))",
        "print(statistics.variance(xs))",
    ]
    expected = "\n".join([str(py_statistics.pvariance(xs)), str(py_statistics.variance(xs))])
    assert _run(tmp_path, "variance.py", _program(body)) == expected + "\n0\n"


def test_quantiles_matches_cpython(tmp_path: Path) -> None:
    xs = [1, 2, 3, 4, 5, 6]
    body = [
        f"xs: list[int] = {xs}",
        "qs: list[float] = statistics.quantiles(xs)",
        "print(qs[0], qs[1], qs[2])",
        "qs2: list[float] = statistics.quantiles(xs, n=2)",
        "print(qs2[0])",
        "qs8: list[float] = statistics.quantiles(xs, n=8)",
        "print(qs8[0], qs8[3], qs8[6])",
    ]
    q4 = py_statistics.quantiles(xs)
    q2 = py_statistics.quantiles(xs, n=2)
    q8 = py_statistics.quantiles(xs, n=8)
    expected = "\n".join(
        [
            f"{q4[0]} {q4[1]} {q4[2]}",
            f"{q2[0]}",
            f"{q8[0]} {q8[3]} {q8[6]}",
        ]
    )
    assert _run(tmp_path, "quantiles.py", _program(body)) == expected + "\n0\n"


def test_the_subset_leaves_the_caller_list_alone(tmp_path: Path) -> None:
    body = [
        "xs: list[int] = [4, 1, 3, 1, 5]",
        "print(statistics.mean(xs))",
        "print(statistics.median(xs))",
        "print(statistics.mode(xs))",
        "print(xs)",
    ]
    got = _run(tmp_path, "no_mutation.py", _program(body))
    assert got.splitlines()[-2] == "[4, 1, 3, 1, 5]"


def test_a_refused_surface_stops_the_compile(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "refused.py",
        "\n".join(
            [
                "import statistics",
                "",
                "def run() -> int:",
                "    xs: list[int] = [1, 2, 3]",
                "    print(statistics.quantiles(xs, n=3))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "PS2003" in got.stderr
