"""`asyncio.ALL_COMPLETED` is the str constant CPython stores.

`run`, `gather`, and `sleep` stay fenced.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests.test_gates import compile_run, run_cli, write_case

_PROBE = """
import asyncio

def checklist() -> int:
    text: str = asyncio.ALL_COMPLETED
    if len(text) == 0:
        return 0
    return len(text) * 1000 + ord(text[0])
"""


def _expected() -> int:
    text = asyncio.ALL_COMPLETED
    if len(text) == 0:
        return 0
    return len(text) * 1000 + ord(text[0])


def test_all_completed_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "all_completed.py", _PROBE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert int(got.stdout.strip()) == _expected()


def test_other_asyncio_names_stay_fenced(tmp_path: Path) -> None:
    cases = [
        ("run.py", "asyncio.run", "asyncio.run is a function and is not a value"),
        ("gather.py", "asyncio.gather", "asyncio.gather is not in the lowered subset of asyncio"),
        ("sleep.py", "asyncio.sleep", "asyncio.sleep is not in the lowered subset of asyncio"),
    ]
    for name, expr, text in cases:
        source = f"import asyncio\ndef probe() -> int:\n    {expr}\n    return 1\n"
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
