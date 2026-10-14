"""`subprocess.DEVNULL` is the int constant CPython stores.

`run`, `Popen`, and `call` stay fenced. `PIPE` and `STDOUT` are int constants.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.test_gates import compile_run, run_cli, write_case

_PROBE = """
import subprocess

def checklist() -> int:
    return subprocess.DEVNULL
"""


def test_devnull_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "devnull.py", _PROBE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert int(got.stdout.strip()) == int(subprocess.DEVNULL)


def test_other_subprocess_names_stay_fenced(tmp_path: Path) -> None:
    cases = [
        ("run.py", "subprocess.run", "subprocess.run is not in the lowered subset of subprocess"),
        ("popen.py", "subprocess.Popen", "subprocess.Popen is not in the lowered subset of subprocess"),
        ("call.py", "subprocess.call", "subprocess.call is not in the lowered subset of subprocess"),
    ]
    for name, expr, text in cases:
        source = f"import subprocess\ndef probe() -> int:\n    return {expr}\n"
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
