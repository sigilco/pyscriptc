"""The interim non-ASCII warning (issue #473, the interim honesty step).

A program that prints a non-ASCII literal writes Latin-1 bytes where
CPython 3.14 writes UTF-8 until the str model lands. The compiler says so
at compile time, as a warning: the program still compiles, the exit code
is unchanged, and an ASCII program stays silent.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_gates import run_cli, write_case

from pyscriptc.lower import lower_source

CAFE = "caf\u00e9"
NAIVE = "na\u00efve"


def test_warning_attaches_to_the_module() -> None:
    module = lower_source(f"def f() -> str:\n    return '{CAFE}'.upper()\n", "w.py")
    notes = module["warnings"]
    assert len(notes) == 1
    assert notes[0]["code"] == "PSW2005"
    assert notes[0]["line"] == 2
    assert "UTF-8" in notes[0]["message"]
    assert "#472" in notes[0]["message"]


def test_warning_is_one_per_line_first_literal_wins() -> None:
    source = f"def f() -> str:\n    return '{CAFE}' + '{CAFE}'\n\n\ndef g() -> str:\n    return '{NAIVE}'\n"
    module = lower_source(source, "w.py")
    notes = module["warnings"]
    assert [note["line"] for note in notes] == [2, 6]


def test_ascii_program_is_silent() -> None:
    module = lower_source("def f() -> str:\n    return 'cafe'\n", "w.py")
    assert module.get("warnings") is None


def test_comment_only_non_ascii_is_silent() -> None:
    module = lower_source(f"# {CAFE}\ndef f() -> int:\n    return 1\n", "w.py")
    assert module.get("warnings") is None


def test_compile_warns_and_exits_zero(tmp_path: Path) -> None:
    path = write_case(tmp_path, "one.py", f"def f() -> str:\n    return '{CAFE}'\n")
    got = run_cli("compile", str(path), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "o.json"))
    assert got.returncode == 0, got.stderr
    assert "PSW2005" in got.stderr
    assert f"{path}:2" in got.stderr
    ir = json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))
    assert ir["warnings"][0]["code"] == "PSW2005"


def test_fenced_program_still_exits_3_without_warning_noise(tmp_path: Path) -> None:
    source = f"def f() -> str:\n    s: str = '{CAFE}5'.replace('x', 'y')\n    return s\n"
    path = write_case(tmp_path, "one.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "o.json"))
    assert got.returncode == 0, got.stderr


def test_direct_non_latin1_fence_does_not_double_report(tmp_path: Path) -> None:
    path = write_case(tmp_path, "one.py", "def f() -> str:\n    return '\u20ac'\n")
    got = run_cli("compile", str(path), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "o.json"))
    assert got.returncode == 3
    assert "PSW2005" not in got.stderr
    assert "PS2005" in got.stderr
