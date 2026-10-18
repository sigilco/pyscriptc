"""`re.DEBUG` is the int constant CPython stores for that flag.

`re.DEBUG` lowers as a constant; the runtime raises ValueError when a
call actually carries it, and the bare member stays a value fence.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from tests.test_gates import compile_run, run_cli, write_case

_PROBE = """
import re

def checklist() -> int:
    return re.DEBUG
"""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_re_debug_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "re_debug.py", _PROBE)
    got = compile_run(tmp_path, path, "checklist")
    assert got.returncode == 0, got.stderr
    assert int(got.stdout.strip()) == int(re.DEBUG)


def test_other_re_flags_stay_fenced(tmp_path: Path) -> None:
    cases = [
        ("search.py", "re.search", "re.search is a function and is not a value"),
    ]
    for name, expr, text in cases:
        source = f"import re\ndef probe() -> int:\n    return {expr}\n"
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
