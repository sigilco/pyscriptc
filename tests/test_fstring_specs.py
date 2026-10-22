"""R11: f-string format specs, `!r`/`!s`/`!a` conversions, `{x=}` debug.

Spec'd f-strings lower to one `str.format`-grammar template plus positional
args, rendered by the same `ps_str_format` engine as `%` and `str.format`.
Each probe joins the renderings so one compile + oracle comparison covers
width/align/precision/type, conversions, debug fields, nested spec fields,
and literal braces.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_SPECS = """
def probe() -> str:
    pi: float = 3.14159
    n: int = 42
    s: str = "hi"
    a: str = f"{pi:.2f}|{pi:10.3f}|{pi:.0f}|{pi:+.2f}|{pi:g}|{pi:.1%}"
    b: str = f"{n:>10}|{n:05d}|{n:<6}|{n:^8}|{n:+#8d}|{n:x}|{n:#b}|{n:08x}|{n:b}"
    c: str = f"{s:>10}|{s:.1}|{s:*^9}|{s:_<7}|{1.5:08.2f}|{-2.5:=8}|{1234567:,}|{255:_b}"
    return a + "/" + b + "/" + c
"""

_CONV_DEBUG = """
def probe() -> str:
    n: int = 42
    s: str = "hi"
    d: dict[str, int] = {"k": 7}
    k: str = "k"
    a: str = f"{n=}|{s=}|{2.5 = :.1f}|{n = }"
    b: str = f"{s!r}|{n!r}|{b'x'!r}|{n!s:>6}|{s!a}|{s!r:>8}"
    c: str = f"{d}|{d[k]:04d}|{{literal}}|{n}"
    return a + "/" + b + "/" + c
"""

_NESTED = """
def probe() -> str:
    w: int = 10
    p: int = 2
    pi: float = 3.14159
    a: str = f"{pi:{w}}|{pi:{w}.{p}f}|{3:{'>'}{5}}"
    b: str = f"{pi:{'.2f'}}|{w:{p}}"
    return a + "/" + b
"""


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_fstring_specs_match_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "fstring_specs.py", _SPECS)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_fstring_conversion_debug_match_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "fstring_conv.py", _CONV_DEBUG)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_fstring_nested_specs_match_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "fstring_nested.py", _NESTED)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


def test_fstring_of_unformattable_stays_fenced(tmp_path: Path) -> None:
    path = write_case(tmp_path, "fstring_list.py", 'def probe() -> str:\n    return f"{[1, 2]:>8}"\n')
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "x.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS2004" in got.stderr, got.stderr


def test_fstring_deep_nesting_stays_fenced(tmp_path: Path) -> None:
    path = write_case(tmp_path, "fstring_deep.py", 'def probe() -> str:\n    return f"{1:{2:{3}}}"\n')
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "x.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS2002" in got.stderr, got.stderr
