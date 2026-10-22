"""Latin-1 str constants on already-lowered modules match the harness fold."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

pytestmark = pytest.mark.linux_container  # HEAVY=1 docker/run.sh

_CASES = (
    ("os", "curdir"),
    ("os", "linesep"),
    ("os", "sep"),
    ("posixpath", "pardir"),
    ("sys", "abiflags"),
    ("sys", "byteorder"),
    ("sys", "copyright"),
    ("inspect", "AGEN_CREATED"),
    ("inspect", "GEN_SUSPENDED"),
    ("asyncio", "FIRST_COMPLETED"),
    ("asyncio", "FIRST_EXCEPTION"),
    ("ssl", "PEM_HEADER"),
    ("ssl", "PEM_FOOTER"),
    ("tempfile", "template"),
)


def _fold(text: str) -> int:
    if len(text) == 0:
        return 0
    return len(text) * 1000 + ord(text[0])


def test_str_surface_matches_cpython(tmp_path: Path) -> None:
    for module, name in _CASES:
        qual = f"{module}.{name}"
        source = (
            f"import {module}\n\n"
            "def checklist() -> int:\n"
            f"    text: str = {qual}\n"
            "    if len(text) == 0:\n"
            "        return 0\n"
            "    return len(text) * 1000 + ord(text[0])\n"
        )
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
        expect = _fold(getattr(importlib.import_module(module), name))
        assert int(got.stdout.strip()) == expect, qual
