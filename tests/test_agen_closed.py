"""`inspect.AGEN_CLOSED` is the str constant CPython stores.

The harness int is `len(text) * 1000 + ord(text[0])`. `signature`,
`getsource`, and `stack` stay fenced.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from tests.test_gates import compile_run, run_cli, write_case

_PROBE = """
import inspect

def checklist() -> int:
    text: str = inspect.AGEN_CLOSED
    if len(text) == 0:
        return 0
    return len(text) * 1000 + ord(text[0])
"""


def _expected() -> int:
    text = inspect.AGEN_CLOSED
    if len(text) == 0:
        return 0
    return len(text) * 1000 + ord(text[0])


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_agen_closed_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "agen_closed.py", _PROBE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert int(got.stdout.strip()) == _expected()


def test_other_inspect_names_stay_fenced(tmp_path: Path) -> None:
    cases = [
        ("signature.py", "inspect.signature", "inspect.signature is not in the lowered subset of inspect"),
        ("getsource.py", "inspect.getsource", "inspect.getsource is not in the lowered subset of inspect"),
        ("stack.py", "inspect.stack", "inspect.stack is not in the lowered subset of inspect"),
    ]
    for name, expr, text in cases:
        source = f"import inspect\ndef probe() -> int:\n    {expr}\n    return 1\n"
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
