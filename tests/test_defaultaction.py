"""`warnings.defaultaction` is the str constant CPython stores.

The harness int is `len(text) * 1000 + ord(text[0])`. `warn`, `filters`,
and `simplefilter` stay fenced.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest
from tests.test_gates import compile_run, run_cli, write_case

_PROBE = """
import warnings

def checklist() -> int:
    text: str = warnings.defaultaction  # type: ignore[attr-defined]
    if len(text) == 0:
        return 0
    return len(text) * 1000 + ord(text[0])

def shown() -> int:
    text: str = warnings.defaultaction  # type: ignore[attr-defined]
    if text == "default":
        return 1
    return 0
"""


def _expected() -> int:
    text = warnings.defaultaction
    if len(text) == 0:
        return 0
    return len(text) * 1000 + ord(text[0])


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_defaultaction_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "defaultaction.py", _PROBE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert int(got.stdout.strip()) == _expected()
    shown = compile_run(tmp_path, path, "shown")
    assert shown.returncode == 0, shown.stderr
    assert shown.stdout.strip() == "1"


def test_other_warnings_names_stay_fenced(tmp_path: Path) -> None:
    cases = [
        ("warn.py", "warnings.warn\n    return 1\n", "warnings.warn is a function and is not a value"),
        ("filters.py", "warnings.filters\n    return 1\n", "warnings.filters is not in the lowered subset of warnings"),
        (
            "simple.py",
            "warnings.simplefilter\n    return 1\n",
            "warnings.simplefilter is not in the lowered subset of warnings",
        ),
    ]
    for name, body, text in cases:
        source = "import warnings\ndef probe() -> int:\n    " + body
        path = write_case(tmp_path, name, source)
        got = run_cli(
            "compile",
            str(path),
            "--skip-check",
            "--emit",
            "llvm",
            "-o",
            str(tmp_path / f"{name}.ll"),
        )
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"
