"""The emitted module text must not depend on PYTHONHASHSEED (D1, #817)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
# Programs that pull synthetic stdlib classes (BytesIO, TextIO, Pattern, ...)
# into the module, which is where set iteration used to leak into the order.
PROGRAMS = ["add.py", "access_log.py", "blob_digest.py"]


def _emit(program: Path, seed: str, out: Path) -> str:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    result = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(program), "--emit", "llvm", "-o", str(out)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return out.read_text()


@pytest.mark.parametrize("name", PROGRAMS)
def test_ll_is_hashseed_independent(name: str, tmp_path: Path) -> None:
    program = EXAMPLES / name
    outputs = {seed: _emit(program, seed, tmp_path / f"{seed}.ll") for seed in ("0", "1", "7")}
    assert len(set(outputs.values())) == 1
