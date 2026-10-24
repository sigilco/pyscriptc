"""The PS2005 fence cases from the str-model plan, diffed against CPython.

Each case pairs the compiler's report with CPython 3.14's answer for the
same source: the fence defects are about what the compiler *reports*, and
the CPython side pins what a correct report protects (the string's code
points, which is why each case names the code point CPython sees).
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


def test_defect_1_one_fence_not_two() -> None:
    """`s` binds when its value fences, so `return s` adds no second fence."""
    source = f"def f() -> str:\n    s: str = 'price: {EUR}5'\n    return s\n"
    assert _codes(source) == [("PS2005", 2)]


def test_defect_1_cascade_still_gone_with_later_reads(tmp_path: Path) -> None:
    path = write_case(tmp_path, "one.py", f"def f() -> str:\n    s: str = 'price: {EUR}5'\n    return s\n")
    got = run_cli("coverage", str(path))
    assert got.returncode == 0, got.stderr
    assert got.stdout.count("PS2002") == 0
    assert "PS2005" in got.stdout


def test_defect_2_eval_fold_fences_not_masks() -> None:
    """`eval("'<non-latin1>'")` reports PS2005, not a misleading eval fence."""
    source = f"def f() -> str:\n    return eval(\"'{EUR}'\")\n"
    assert _codes(source) == [("PS2005", 2)]


def test_defect_2_fold_result_fences() -> None:
    """A fold whose CPython result is non-Latin-1 (ord of a char above 255) fences."""
    value = _cpython_value("'\\u20ac'")
    assert value == EUR
    source = f"def f() -> str:\n    return chr(ord('{EUR}'))\n"
    assert _codes(source) == [("PS2005", 2)]


def test_defect_2_repr_of_non_latin1_fences() -> None:
    source = f"def f() -> str:\n    return repr('{EUR}')\n"
    assert _codes(source) == [("PS2005", 2)]


def test_latin1_literal_is_not_a_fence_yet() -> None:
    """`é` is inside Latin-1, so it lowers; the divergence is item 3, not a fence."""
    source = f"def f() -> str:\n    s: str = '{CAFE}'\n    return s\n"
    assert _codes(source) == []


def test_non_latin1_literal_direct_fence() -> None:
    source = f"def f() -> str:\n    return '{EUR}'.upper()\n"
    assert _codes(source) == [("PS2005", 2)]


def test_cpython_sees_the_code_points(tmp_path: Path) -> None:
    """The CPython side of the differential: the same literals are code points."""
    assert len(ast.literal_eval(f"'{CAFE}'")) == 4
    assert _cpython_value(f"'{CAFE}'.upper()") == CAFE.upper()
    assert _cpython_value("'\\u20ac'") == EUR


def test_compile_exits_3_with_one_fence(tmp_path: Path) -> None:
    path = write_case(tmp_path, "one.py", f"def f() -> str:\n    s: str = 'price: {EUR}5'\n    return s\n")
    got = run_cli("compile", str(path), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "o.json"))
    assert got.returncode == 3
    assert got.stderr.count("PS2005") == 1
    assert "PS2002" not in got.stderr


def test_latin1_program_still_compiles_clean(tmp_path: Path) -> None:
    path = write_case(tmp_path, "one.py", f"def f() -> str:\n    return '{CAFE}'.upper()\n")
    got = run_cli("compile", str(path), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "o.json"))
    assert got.returncode == 0, got.stderr
