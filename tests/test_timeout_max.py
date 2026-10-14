"""`threading.TIMEOUT_MAX` is the float const CPython stores.

The harness returns 2 when the value compares unequal to itself, and 1
otherwise. `Thread`, `Lock`, and `Event` stay fenced.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from tests.test_gates import compile_run, run_cli, write_case

_PROBE = """
import threading

def checklist() -> int:
    value: float = threading.TIMEOUT_MAX
    if value != value:
        return 2
    return 1
"""


def _expected() -> int:
    value = threading.TIMEOUT_MAX
    if value != value:
        return 2
    return 1


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_timeout_max_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "timeout_max.py", _PROBE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert int(got.stdout.strip()) == _expected()


def test_other_threading_names_stay_fenced(tmp_path: Path) -> None:
    cases = [
        ("thread.py", "threading.Thread", "threading.Thread is not in the lowered subset of threading"),
        ("lock.py", "threading.Lock", "threading.Lock is not in the lowered subset of threading"),
        ("event.py", "threading.Event", "threading.Event is not in the lowered subset of threading"),
    ]
    for name, expr, text in cases:
        source = f"import threading\ndef probe() -> int:\n    {expr}\n    return 1\n"
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
