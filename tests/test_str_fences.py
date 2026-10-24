"""The S2 lowering reality: non-ASCII lowers, lone surrogates fence.

The Latin-1 fence and the interim PSW2005 warning are gone (issue #474):
a str literal is a code-point sequence stored UTF-8, so any scalar code
point lowers and only a lone surrogate has no bytes to store. These tests
pin the new behavior and the CPython agreement behind it.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from tests.test_gates import run_cli, write_case

from pyscriptc.lower import lower_source

EUR = "\u20ac"
CAFE = "caf\u00e9"


def _codes(source: str) -> list[tuple[str, int]]:
    module = lower_source(source, "case.py")
    return [(fence["code"], fence["span"]["line"]) for fence in module["fences"]]


def _cpython_value(source: str) -> str:
    """CPython's own answer for the expression, run on this interpreter."""
    program = f"print({source})"  # noqa: S607
    got = subprocess.run(  # noqa: S603
        [sys.executable, "-c", program], capture_output=True, text=True, check=True
    )
    return got.stdout.strip()


def test_non_ascii_literal_lowers_clean() -> None:
    """`é` and `€` are code points with UTF-8 bytes: they lower, no fence."""
    source = f"def f() -> str:\n    s: str = 'price: {EUR}5 {CAFE}'\n    return s\n"
    assert _codes(source) == []


def test_only_lone_surrogates_fence() -> None:
    """A lone surrogate has no UTF-8 bytes: PS2005 at the literal."""
    source = "def f() -> str:\n    return '\\ud800'.upper()\n"
    assert _codes(source) == [("PS2005", 2)]


def test_paired_surrogate_escape_is_alone() -> None:
    """Two escapes are two lone surrogates: CPython also refuses to encode."""
    source = "def f() -> str:\n    return '\\ud83d\\ude00'\n"
    assert _codes(source) == [("PS2005", 2)]


def test_cascade_gone_with_later_reads(tmp_path: Path) -> None:
    """`s` binds when its value lowers, so `return s` adds no fence."""
    path = write_case(tmp_path, "one.py", f"def f() -> str:\n    s: str = 'price: {EUR}5'\n    return s\n")
    got = run_cli("coverage", str(path))
    assert got.returncode == 0, got.stderr
    assert got.stdout.count("PS2005") == 0
    assert got.stdout.count("PS2002") == 0


def test_eval_fold_of_non_ascii_lowers() -> None:
    """`eval("'<non-ascii>'")` folds to the str constant and lowers."""
    source = f"def f() -> str:\n    return eval(\"'{EUR}'\")\n"
    assert _codes(source) == []


def test_ord_chr_of_non_ascii_lowers() -> None:
    """`chr(ord('€'))` folds through CPython's answer and lowers."""
    value = _cpython_value("'\\u20ac'")
    assert value == EUR
    source = f"def f() -> str:\n    return chr(ord('{EUR}'))\n"
    assert _codes(source) == []


def test_repr_of_non_ascii_lowers() -> None:
    source = f"def f() -> str:\n    return repr('{EUR}')\n"
    assert _codes(source) == []


def test_non_ascii_method_calls_lower() -> None:
    """The methods the S2 runtime owns lower on non-ASCII operands."""
    for expr, typ in (
        (f"'{CAFE}'.upper()", "str"),
        (f"'{EUR}'.lower()", "str"),
        (f"'{CAFE}'.find('f')", "int"),
        (f"len('{CAFE}')", "int"),
        (f"'{CAFE}'[1]", "str"),
        (f"'{CAFE}'.startswith('caf')", "bool"),
        (f"'{CAFE}'.replace('f', 'g')", "str"),
    ):
        assert _codes(f"def f() -> {typ}:\n    return {expr}\n") == []


def test_cpython_sees_the_code_points() -> None:
    """The CPython side of the differential: the same literals are code points."""
    assert len(ast.literal_eval(f"'{CAFE}'")) == 4
    assert _cpython_value(f"'{CAFE}'.upper()") == CAFE.upper()
    assert _cpython_value("'\\u20ac'") == EUR


def test_compile_exits_zero_with_no_fence(tmp_path: Path) -> None:
    path = write_case(tmp_path, "one.py", f"def f() -> str:\n    s: str = 'price: {EUR}5'\n    return s\n")
    got = run_cli("compile", str(path), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "o.json"))
    assert got.returncode == 0, got.stderr
    assert "PS2005" not in got.stderr
    assert "PS2002" not in got.stderr


def test_surrogate_fence_exits_3(tmp_path: Path) -> None:
    path = write_case(tmp_path, "one.py", "def f() -> str:\n    return '\\ud800'\n")
    got = run_cli("compile", str(path), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "o.json"))
    assert got.returncode == 3
    assert got.stderr.count("PS2005") == 1
    assert "PSW2005" not in got.stderr


def test_ir_carries_the_utf8_constant(tmp_path: Path) -> None:
    """The IR's str constant is the code-point text, no warning key."""
    source = f"def f() -> str:\n    return '{CAFE}'\n"
    module = lower_source(source, "one.py")
    assert module["fences"] == []
    assert "warnings" not in module
