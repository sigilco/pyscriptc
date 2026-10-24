"""The interim PSW2005 warning is retired (issue #474, S2).

S1 warned that non-ASCII text ran through the Latin-1 model; S2 lands the
code-point runtime, so a non-ASCII program compiles silent and correct.
The module keeps its name until the board item closes, and asserts the
retirement: no warning key in the IR, no stderr noise, any exit code.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_gates import run_cli, write_case

from pyscriptc.lower import lower_source

CAFE = "caf\u00e9"
NAIVE = "na\u00efve"


def test_no_warning_key_in_the_ir() -> None:
    module = lower_source(f"def f() -> str:\n    return '{CAFE}'.upper()\n", "w.py")
    assert module["fences"] == []
    assert "warnings" not in module


def test_two_lines_stay_silent() -> None:
    source = f"def f() -> str:\n    return '{CAFE}' + '{CAFE}'\n\n\ndef g() -> str:\n    return '{NAIVE}'\n"
    module = lower_source(source, "w.py")
    assert module["fences"] == []
    assert "warnings" not in module


def test_ascii_program_is_silent() -> None:
    module = lower_source("def f() -> str:\n    return 'cafe'\n", "w.py")
    assert module["fences"] == []
    assert "warnings" not in module


def test_compile_is_quiet_and_exits_zero(tmp_path: Path) -> None:
    path = write_case(tmp_path, "one.py", f"def f() -> str:\n    return '{CAFE}'\n")
    got = run_cli("compile", str(path), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "o.json"))
    assert got.returncode == 0, got.stderr
    assert "PSW2005" not in got.stderr
    assert "warning" not in got.stderr.lower()
    ir = json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))
    assert "warnings" not in ir


def test_fenced_program_exits_3_without_warning_noise(tmp_path: Path) -> None:
    source = "def f() -> str:\n    s: str = '\\ud800'.replace('x', 'y')\n    return s\n"
    path = write_case(tmp_path, "one.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "o.json"))
    assert got.returncode == 3
    assert "PSW2005" not in got.stderr
    assert "PS2005" in got.stderr
