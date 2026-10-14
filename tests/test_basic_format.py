"""`logging.BASIC_FORMAT` is the str constant CPython stores.

`%` and parentheses in that string are characters. `basicConfig`,
`getLogger`, and `Handler` stay fenced.
"""

from __future__ import annotations

import logging
from pathlib import Path

from tests.test_gates import compile_run, run_cli, write_case

_PROBE = """
import logging

def checklist() -> int:
    text: str = logging.BASIC_FORMAT
    if len(text) == 0:
        return 0
    return len(text) * 1000 + ord(text[0])
"""


def _expected() -> int:
    text = logging.BASIC_FORMAT
    if len(text) == 0:
        return 0
    return len(text) * 1000 + ord(text[0])


def test_basic_format_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "basic_format.py", _PROBE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert int(got.stdout.strip()) == _expected()


def test_other_logging_names_stay_fenced(tmp_path: Path) -> None:
    cases = [
        ("config.py", "logging.basicConfig", "logging.basicConfig is not in the lowered subset of logging"),
        ("logger.py", "logging.getLogger", "logging.getLogger is not in the lowered subset of logging"),
        ("handler.py", "logging.Handler", "logging.Handler is not in the lowered subset of logging"),
    ]
    for name, expr, text in cases:
        source = f"import logging\ndef probe() -> int:\n    {expr}\n    return 1\n"
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
