"""Issue #409: the library receipt, a deps-driven certifi program matches CPython.

`benchmarks/library/certifi_receipt.py` imports `where` and `contents`
from a real certifi wheel pulled from PyPI through the deps lane, the
compiler lowers it from the unpacked wheel source, and the wheel's
`cacert.pem` is staged beside the emitted binary. The test runs the same
script under CPython with the unpacked wheel's source root on
`PYTHONPATH` and requires the two outputs to agree. Opt-in: it talks to
PyPI every run (the tmp deps cache is cold) and compiles a binary.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc import cli, deps

SCRIPT = Path(__file__).resolve().parents[1] / "benchmarks" / "library" / "certifi_receipt.py"

needs_network = pytest.mark.skipif(
    os.environ.get("PYSC_NETWORK_TESTS") != "1" or shutil.which("uv") is None or not SCRIPT.is_file(),
    reason="network test: set PYSC_NETWORK_TESTS=1 and install uv (uv resolves certifi)",
)
needs_toolchain = pytest.mark.skipif(
    shutil.which("zig") is None and shutil.which("clang") is None,
    reason="needs zig or clang on PATH (the receipt compiles a binary)",
)


@needs_network
@needs_toolchain
def test_certifi_library_receipt_matches_cpython(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The compiled binary and CPython print the same thing for the receipt script."""
    monkeypatch.delenv("PYSC_UV", raising=False)
    monkeypatch.setenv("PYSC_NO_TELEMETRY", "1")
    monkeypatch.setenv("PYSC_CACHE_DIR", str(tmp_path / "cache"))

    report = deps.run_deps(SCRIPT)
    (certifi,) = [p for p in report["packages"] if p["name"] == "certifi"]
    assert certifi["lane"] == "pure", certifi["reason"]
    source_root = Path(report["sourceRoots"][0])

    env = {**os.environ, "PYTHONPATH": str(source_root)}
    reference = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,  # noqa: S603
    )
    assert reference.returncode == 0, reference.stderr

    binary = tmp_path / "certifi-receipt"
    assert cli.main(["compile", str(SCRIPT), "--skip-check", "--run", "--emit", "bin", "-o", str(binary)]) == 0

    staged = tmp_path / "certifi-receipt-data" / "certifi" / "cacert.pem"
    assert staged.is_file(), "the wheel's data file is staged under the dotted path"

    probe = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    assert probe.stdout == reference.stdout, (probe.stdout, reference.stdout, probe.stderr)
    assert probe.returncode == reference.returncode

    ir = Path(f"{binary}.ir.json")
    fences = json.loads(ir.read_text(encoding="utf-8"))["fences"]
    assert fences == []
