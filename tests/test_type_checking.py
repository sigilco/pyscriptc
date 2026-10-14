"""A module if TYPE_CHECKING is omitted. CPython treats that name as false.

The dead branch imports a module this compiler would fence. The live branch
assigns a str constant. A user assignment of the same name is not this fold.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
import sys
import typing
import typing_extensions
from typing import TYPE_CHECKING
from typing_extensions import TYPE_CHECKING as TC

if TYPE_CHECKING:
    import argparse
else:
    left: str = "Ada"

if not TYPE_CHECKING:
    mid: str = "Lovelace"
else:
    import zipfile

if typing.TYPE_CHECKING:
    import http.client
else:
    attr: str = "X"

if not typing_extensions.TYPE_CHECKING:
    ext: str = "Y"
else:
    import asyncio

if TC:
    from collections.abc import Sequence
else:
    alias: str = "Z"

if sys.version_info >= (3, 8):
    taken: str = "Q"
elif TYPE_CHECKING:
    import socket
else:
    import tkinter

def probe() -> str:
    return left + " " + mid + attr + ext + alias + taken  # type: ignore[name-defined]
"""


def test_type_checking_branch_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "type_checking.py", _PROBE)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "Ada LovelaceXYZQ"


def test_user_type_checking_name_stays_a_module_if(tmp_path: Path) -> None:
    cases = [
        (
            "assigned.py",
            "\n".join(
                [
                    "from typing import TYPE_CHECKING",
                    "TYPE_CHECKING = True",
                    "if TYPE_CHECKING:",
                    "    import argparse",
                    "mark: str = 'Ada'",
                    "def probe() -> str:",
                    "    return mark",
                    "",
                ]
            ),
        ),
        (
            "local.py",
            "\n".join(
                [
                    "TYPE_CHECKING = False",
                    "if TYPE_CHECKING:",
                    "    import argparse",
                    "else:",
                    "    mark: str = 'Ada'",
                    "def probe() -> str:",
                    "    return mark",
                    "",
                ]
            ),
        ),
    ]
    for name, source in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert "If at module scope is not lowered" in got.stderr
