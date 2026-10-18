"""R16: the full `%` spec set and `str.format` run one runtime engine.

Each probe joins the renderings so one compile + oracle comparison covers
width, precision, flags, grouping, conversions, keyed `%(name)` fields,
kwargs, item fields, and nested spec interpolation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PERCENT = """
def probe() -> str:
    a: str = "%05d|%-5d|%+d|% d|%5.3d|%hd|%i" % (3, 7, 9, 9, 5, 300, 2)
    b: str = "%x|%#x|%x|%o|%#o" % (255, 255, -1, 8, 8)
    c: str = "%.2f|%10.3e|%g|%G|%f" % (1234.56789, 1234.56789, 1.0, 1e-7, 2.5)
    d: str = "%*d|%.*f|%5.2s|%r|%a|%c|%5c" % (6, 4, 2, 2.34567, "abcdef", "it's", "x", 65, 66)
    e: str = "%s|%d|%r" % (True, True, True)
    f: str = "%r|%a" % (b"ab", b"ab")
    g: str = "%d|%s|%s%%" % (1.9, "end", "!")
    return a + "/" + b + "/" + c + "/" + d + "/" + e + "/" + f + "/" + g
"""

_MAP = """
def probe() -> str:
    d: dict[str, int] = {"k": 2}
    s: dict[int, str] = {1: "x"}
    a: str = "%s" % {"a": 1, "b": 2}
    b: str = "%(k)05d|%(k)r" % d
    c: str = "%s" % s
    return a + "/" + b + "/" + c
"""

_DOT = """
def probe() -> str:
    a: str = "{}-{}|{a} {}".format(1, "a", 2, a=7)
    b: str = "{0} {0} {1}".format(3, "q")
    c: str = "{:08.2f}|{:>06.2f}|{:=8}|{:+08.2f}".format(-1.5, -1.5, -3, 1.5)
    d: str = "{:*>8}|{:^9}|{:,}|{:_}|{:#_x}".format("hi", "ab", 1234567, 255, 255)
    e: str = "{:_b}|{:.1%}|{:.3}|{:g}|{:n}".format(255, 0.5, 1.0, 1.0, 1.0)
    f: str = "{1[0]}|{0[k]}|{2:{3}}".format({"k": 9}, "zq", 3, "5d")
    g: str = "{!r}|{!r}".format("r", b"x")
    h: str = "{:>6}|{}|{:d}".format(True, True, True)
    i: str = "{}".format({"m": 1})
    return a + "/" + b + "/" + c + "/" + d + "/" + e + "/" + f + "/" + g + "/" + h + "/" + i
"""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_percent_specs_match_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "percent_specs.py", _PERCENT)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_percent_mapping_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "percent_map.py", _MAP)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_str_format_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "str_format.py", _DOT)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


def test_percent_of_unformattable_stays_fenced(tmp_path: Path) -> None:
    path = write_case(tmp_path, "percent_list.py", 'def probe() -> str:\n    return "%s" % [1, 2]\n')
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "x.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS2004" in got.stderr, got.stderr


def test_format_kwargs_unpack_stays_fenced(tmp_path: Path) -> None:
    path = write_case(tmp_path, "format_unpack.py", 'def probe() -> str:\n    return "{a}".format(**{"a": 1})\n')
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "x.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS2003" in got.stderr, got.stderr
