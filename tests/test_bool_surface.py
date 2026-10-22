"""Bool constants on already-lowered modules match the harness `if` fold."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

pytestmark = pytest.mark.linux_container  # HEAVY=1 docker/run.sh

_CASES = (
    ("os", "supports_bytes_environ"),
    ("posixpath", "supports_unicode_filenames"),
    ("sys", "dont_write_bytecode"),
    ("logging", "raiseExceptions"),
    ("logging", "logThreads"),
    ("socket", "has_ipv6"),
    ("ssl", "HAS_ALPN"),
    ("ssl", "HAS_NPN"),
    ("ssl", "HAS_SSLv2"),
    ("ssl", "HAS_TLSv1_3"),
)


def test_bool_surface_matches_cpython(tmp_path: Path) -> None:
    for module, name in _CASES:
        qual = f"{module}.{name}"
        source = f"import {module}\n\ndef checklist() -> int:\n    if {qual}:\n        return 1\n    return 0\n"
        path = write_case(tmp_path, f"{module}_{name}.py", source)
        got = run_cli(
            "compile",
            str(path),
            "--skip-check",
            "--run",
            "--call",
            "checklist",
            "-o",
            str(tmp_path / f"{module}_{name}"),
        )
        assert got.returncode == 0, f"{qual}\n{got.stderr}"
        expect = 1 if getattr(importlib.import_module(module), name) else 0
        assert int(got.stdout.strip()) == expect, qual
