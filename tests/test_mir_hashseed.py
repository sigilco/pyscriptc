"""The MIR lane's lower+emit output must not depend on PYTHONHASHSEED.

G-2 from cloud-agent handoff 3. Mirrors `tests/test_ll_hashseed.py`: each
`tests/fixtures/mir_leaf/` fixture is lowered and emitted under three hash
seeds, and the canonical MIR text plus the LLVM text must match.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_FIXTURES = Path(__file__).parent / "fixtures" / "mir_leaf"
LEAF_SAMPLES = sorted(p.name for p in _FIXTURES.glob("*.py"))
SEEDS = ("0", "1", "7")

_DRIVER = r"""
from __future__ import annotations

import sys

from pyscriptc.lower import lower_file
from pyscriptc.mir import module_hash, module_serialize
from pyscriptc.miremit import emit_llvm_mir
from pyscriptc.mirlower import lower_module

path = sys.argv[1]
module, _ = lower_file(path)
mmod = lower_module(module)
sys.stdout.write(module_hash(mmod))
sys.stdout.write("\n")
sys.stdout.write(module_serialize(mmod))
sys.stdout.write("\n--LLVM--\n")
sys.stdout.write(emit_llvm_mir(module))
"""


def _lower_and_emit(program: Path, seed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": seed, "PYSC_MIR": "1"}
    result = subprocess.run(
        [sys.executable, "-c", _DRIVER, str(program)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("name", LEAF_SAMPLES)
def test_mir_lane_is_hashseed_independent(name: str) -> None:
    program = _FIXTURES / name
    outputs = {seed: _lower_and_emit(program, seed) for seed in SEEDS}
    assert len(set(outputs.values())) == 1
    assert "--LLVM--" in next(iter(outputs.values()))
