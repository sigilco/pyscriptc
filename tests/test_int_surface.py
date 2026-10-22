"""Int constants on modules that were already lowered match CPython."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

pytestmark = pytest.mark.linux_container  # HEAVY=1 docker/run.sh

# One name from each shape: flag, negative, wide i64, and a plain errno re-export.
_CASES = (
    ("re", "I"),
    ("re", "S"),
    ("subprocess", "PIPE"),
    ("subprocess", "STDOUT"),
    ("errno", "ENODEV"),
    ("os", "O_RDONLY"),
    ("stat", "SF_SETTABLE"),
    ("socket", "INADDR_BROADCAST"),
    ("ssl", "OP_ALL"),
    ("sys", "hexversion"),
    ("base64", "MAXBINSIZE"),
    ("shutil", "COPY_BUFSIZE"),
    ("pathlib", "E2BIG"),
    ("logging", "INFO"),
    ("time", "timezone"),
    ("tempfile", "TMP_MAX"),
    ("datetime", "MAXYEAR"),
    ("io", "SEEK_CUR"),
    ("inspect", "CO_OPTIMIZED"),
    ("ctypes", "RTLD_GLOBAL"),
)


def test_int_surface_matches_cpython(tmp_path: Path) -> None:
    for module, name in _CASES:
        qual = f"{module}.{name}"
        source = f"import {module}\n\ndef checklist() -> int:\n    return {qual}\n"
        path = write_case(tmp_path, f"{module.replace('.', '_')}_{name}.py", source)
        got = run_cli(
            "compile",
            str(path),
            "--skip-check",
            "--run",
            "--call",
            "checklist",
            "-o",
            str(tmp_path / f"{module.replace('.', '_')}_{name}"),
        )
        assert got.returncode == 0, f"{qual}\n{got.stderr}"
        expect = int(getattr(importlib.import_module(module), name))
        assert int(got.stdout.strip()) == expect, qual
