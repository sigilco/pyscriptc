"""R20 builtin pack: divmod, hex/oct/bin, pow, repr, sorted(reverse=),
sum(..., start), and min/max over iterables or with key=.

Each oracle test compiles a probe and diffs its stdout against CPython.
`divmod` needs a printable tuple, so `print` of a synthetic tuple class writes
CPython's `(a, b)` shape field by field.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tests.test_gates import oracle, run_cli, write_case

from pyscriptc.lower import lower_source


def _oracle_diff(tmp_path: Path, name: str, source: str, func: str = "probe") -> None:
    path = write_case(tmp_path, name, source)
    out = str(tmp_path / name.removesuffix(".py"))
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", func, "-o", out)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func)


def test_divmod_pairs_and_prints(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r20_divmod.py",
        "\n".join(
            [
                "def probe() -> int:",
                "    print(divmod(7, 3))",
                "    print(divmod(7.5, 2))",
                "    print(divmod(-7, 3))",
                "    q, r = divmod(7, 3)",
                "    return q * 10 + r",
                "",
            ]
        ),
    )


def test_hex_oct_bin(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r20_bases.py",
        "\n".join(
            [
                "def probe() -> str:",
                "    parts: list[str] = [hex(255), oct(8), bin(5), hex(0), hex(-255), bin(-1)]",
                '    return " ".join(parts)',
                "",
            ]
        ),
    )


def test_pow_two_and_three_args(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r20_pow.py",
        "\n".join(
            [
                "def probe() -> str:",
                "    parts: list[str] = [str(pow(2, 10)), str(pow(2, 0.5)), str(pow(2, 10, 7)), str(pow(3, 4, 5))]",
                '    return " ".join(parts)',
                "",
            ]
        ),
    )


def test_repr_scalars_and_dict(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r20_repr.py",
        "\n".join(
            [
                "def probe() -> str:",
                '    d: dict[str, int] = {"a": 1}',
                '    parts: list[str] = [repr(42), repr(1.5), repr("hi"), repr(True), repr(b"ab"), repr(d)]',
                '    return " ".join(parts)',
                "",
            ]
        ),
    )


def test_sorted_reverse_matches_cpython(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r20_sorted_rev.py",
        "\n".join(
            [
                "def probe() -> str:",
                "    xs: list[int] = [3, 1, 2]",
                "    r: list[int] = sorted(xs, reverse=True)",
                '    ws: list[str] = ["bb", "a", "ccc"]',
                "    z: list[str] = sorted(ws, key=lambda w: len(w), reverse=True)",
                '    return " ".join([str(r[0]), str(r[1]), str(r[2]), z[0], z[1], z[2]])',
                "",
            ]
        ),
    )


def test_sorted_reverse_is_stable(tmp_path: Path) -> None:
    # Equal keys keep original order under reverse, like CPython.
    _oracle_diff(
        tmp_path,
        "r20_sorted_rev_stable.py",
        "\n".join(
            [
                "def probe() -> str:",
                '    ws: list[str] = ["dd", "bb", "a", "e", "cc"]',
                "    z: list[str] = sorted(ws, key=lambda w: len(w), reverse=True)",
                '    return " ".join(z)',
                "",
            ]
        ),
    )


def test_sum_with_start(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r20_sum.py",
        "\n".join(
            [
                "def probe() -> str:",
                "    xs: list[int] = [1, 2, 3]",
                "    fs: list[float] = [1.0, 2.0]",
                "    parts: list[str] = [str(sum(xs)), str(sum(xs, 10)),",
                "        str(sum(xs, start=100)), str(sum(fs, start=0.5))]",
                '    return " ".join(parts)',
                "",
            ]
        ),
    )


def test_min_max_iterables_and_varargs(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r20_minmax.py",
        "\n".join(
            [
                "def probe() -> str:",
                "    xs: list[int] = [3, 1, 2]",
                "    fs: list[float] = [2.5, 0.5]",
                '    ws: list[str] = ["bb", "a", "ccc"]',
                '    d: dict[str, int] = {"b": 1, "a": 2}',
                "    parts: list[str] = [",
                "        str(min(xs)), str(max(xs)), str(min(3, 1, 2)), str(max(3, 1, 2)),",
                "        str(min(fs)), str(max(fs)), min(ws), max(ws), min(d), max(d),",
                "        str(min(2, 1)), str(max(2, 1)),",
                "    ]",
                '    return " ".join(parts)',
                "",
            ]
        ),
    )


def test_min_max_key_matches_cpython(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r20_minmax_key.py",
        "\n".join(
            [
                "def probe() -> str:",
                '    ws: list[str] = ["bb", "a", "ccc"]',
                "    xs: list[int] = [3, 1, 2]",
                "    parts: list[str] = [",
                "        min(ws, key=lambda w: len(w)), max(ws, key=lambda w: len(w)),",
                "        str(min(xs, key=lambda v: 0 - v)), str(max(xs, key=lambda v: 0 - v)),",
                "    ]",
                '    return " ".join(parts)',
                "",
            ]
        ),
    )


def test_min_empty_raises_valueerror(tmp_path: Path) -> None:
    path = write_case(tmp_path, "r20_min_empty.py", "xs: list[int] = []\nprint(min(xs))\n")
    got = run_cli("compile", str(path), "--run", "-o", str(tmp_path / "r20_min_empty"))
    assert got.returncode != 0
    assert "ValueError: min() iterable argument is empty" in got.stderr


def test_max_key_empty_raises_valueerror(tmp_path: Path) -> None:
    path = write_case(tmp_path, "r20_max_empty.py", "xs: list[int] = []\nprint(max(xs, key=lambda v: v))\n")
    got = run_cli("compile", str(path), "--run", "-o", str(tmp_path / "r20_max_empty"))
    assert got.returncode != 0
    assert "ValueError: max() iterable argument is empty" in got.stderr


def test_repr_of_list_still_fences() -> None:
    mod = lower_source(
        """
def probe(xs: list[int]) -> str:
    return repr(xs)
"""
    )
    assert len(mod["fences"]) == 1
    assert mod["fences"][0]["code"] == "PS2004"


def test_hex_of_str_still_fences() -> None:
    mod = lower_source(
        """
def probe(w: str) -> str:
    return hex(w)
"""
    )
    assert len(mod["fences"]) == 1
    assert mod["fences"][0]["code"] == "PS1003"


def test_sum_of_str_still_fences() -> None:
    mod = lower_source(
        """
def probe(ws: list[str]) -> int:
    return sum(ws)
"""
    )
    assert len(mod["fences"]) == 1
    assert mod["fences"][0]["code"] == "PS2003"


if __name__ == "__main__":
    sys.exit(0)
