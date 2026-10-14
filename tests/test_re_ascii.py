"""`re.ASCII` is the same int constant as `re.A`."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from tests.test_gates import compile_run, write_case

pytestmark = pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh

_PROBE = """
import re

def checklist() -> int:
    return re.ASCII

def same() -> int:
    if re.ASCII == re.A:
        return re.ASCII
    return 0
"""


def test_re_ascii_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "re_ascii.py", _PROBE)
    for func in ("checklist", "same"):
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert int(got.stdout.strip()) == int(re.ASCII)
