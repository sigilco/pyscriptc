"""Checker preflight.

mypy is a subprocess. Its AST is not an input to lowering. A bad annotation
fails the build before LLVM emission.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


class CheckFailed(Exception):
    def __init__(self, output: str) -> None:
        super().__init__(output)
        self.output = output


def run_checker(path: Path, checker: str = "mypy") -> None:
    if checker != "mypy":
        raise CheckFailed(f"checker {checker!r} is not wired; the spike preflight uses mypy")
    with tempfile.TemporaryDirectory(prefix="pyscriptc-mypy-") as cache:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "mypy",
                "--show-error-codes",
                "--hide-error-context",
                "--no-error-summary",
                "--follow-imports=silent",
                "--cache-dir",
                cache,
                "--no-incremental",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
    if proc.returncode == 0:
        return
    output = (proc.stdout or proc.stderr).strip()
    if not output:
        output = proc.stderr.strip() or f"mypy exited {proc.returncode}"
    if "No module named mypy" in output:
        output = "mypy is not installed for this interpreter"
    raise CheckFailed(output)
