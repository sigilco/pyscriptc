"""S1: the F2 ffiprobe compiles on the direct lane and the MIR lane.

Cloud-handoff spike: the FFI data plane must still build when `PYSC_MIR=1`
opts into the strangler emitter. The ABI call is out of the leaf slice
and rides the direct path; the linked binary still prints the C sum.
Compiler code is unchanged; this file only pins the dual-lane receipt.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc.emit.llvm import emit_llvm
from pyscriptc.ffi_manifest import load_manifest
from pyscriptc.lower import lower_source
from pyscriptc.miremit import emit_llvm_mir
from pyscriptc.mirlower import lower_module, supported_fn
from pyscriptc.stdlib_queue import ffi_modules

ROOT = Path(__file__).resolve().parents[1]
FFIPROBE = ROOT / "packaging" / "zig" / "ffiprobe"

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")

MIXED = """import ffiprobe


def total() -> int:
    return ffiprobe.add3(20, 21, 22)


def leaf(a: int, b: int) -> int:
    return a + b
"""


def _lower_mixed() -> dict:
    manifest = load_manifest(FFIPROBE / "ffi.toml")
    with ffi_modules([manifest]):
        return lower_source(MIXED, "use.py")


def test_ffi_caller_is_out_of_the_mir_leaf_slice() -> None:
    """An ABI call is not a leaf expression, so the caller is skipped."""
    module = _lower_mixed()
    assert module["fences"] == []
    by_name = {fn["name"]: fn for fn in module["functions"]}
    assert supported_fn(by_name["total"]) is False
    assert supported_fn(by_name["leaf"]) is True
    mir = lower_module(module)
    assert [fn.name for fn in mir.functions] == ["leaf"]
    assert mir.skipped == ["total"]


def test_mixed_ffi_and_leaf_llvm_is_byte_identical() -> None:
    """Splice of in-slice leaf + skipped FFI caller matches the direct emit."""
    module = _lower_mixed()
    assert emit_llvm_mir(module) == emit_llvm(module)


def _compile_probe(tmp_path: Path, *, mir: bool) -> subprocess.CompletedProcess[str]:
    source = tmp_path / "use.py"
    source.write_text(
        "import ffiprobe\n\ndef total() -> int:\n    return ffiprobe.add3(20, 21, 22)\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    if mir:
        env["PYSC_MIR"] = "1"
    else:
        env.pop("PYSC_MIR", None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(source),
            "--ffi",
            str(FFIPROBE / "ffi.toml"),
            "--skip-check",
            "--run",
            "--call",
            "total",
            "--args",
            "[]",
            "-o",
            str(tmp_path / ("mir.bin" if mir else "direct.bin")),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


@needs_clang
def test_ffiprobe_runs_on_the_direct_lane(tmp_path: Path) -> None:
    proc = _compile_probe(tmp_path, mir=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "63\n"


@needs_clang
def test_ffiprobe_runs_on_the_mir_lane(tmp_path: Path) -> None:
    proc = _compile_probe(tmp_path, mir=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "63\n"
