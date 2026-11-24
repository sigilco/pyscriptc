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
    assert (
        cli.main(
            [
                "compile",
                str(SCRIPT),
                "--skip-check",
                "--run",
                "--emit",
                "bin",
                "-o",
                str(binary),
                "--debug-dir",
                str(tmp_path / "dbg"),
            ]
        )
        == 0
    )

    staged = tmp_path / "certifi-receipt-data" / "certifi" / "cacert.pem"
    assert staged.is_file(), "the wheel's data file is staged under the dotted path"

    probe = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    assert probe.stdout == reference.stdout, (probe.stdout, reference.stdout, probe.stderr)
    assert probe.returncode == reference.returncode

    ir = tmp_path / "dbg" / "ir.json"
    fences = json.loads(ir.read_text(encoding="utf-8"))["fences"]
    assert fences == []


# Pilot 2 (issue #417): iniconfig, a real parser with real types, from PyPI.
PILOT2 = SCRIPT.parent / "iniconfig_pilot.py"

# The reachable fence ceiling of the pilot, measured 2026-11-23 on iniconfig
# 2.3.1: 33 on master `69b54156`, 27 once the bare-`Final` and optional-tuple
# slices of #417 landed. Lower it as each follow-up slice lands; the strict
# xfail below turns into a failure the day the profile reaches zero, so the
# full CPython match gets promoted instead of staying silently expected.
PILOT2_FENCE_CEILING = 27


def _pilot2_profile(tmp_path: Path) -> tuple[dict, Path]:
    from benchmarks.library.pilot2 import fence_profile

    report = deps.run_deps(PILOT2)
    (iniconfig,) = [p for p in report["packages"] if p["name"] == "iniconfig"]
    assert iniconfig["lane"] == "pure", iniconfig["reason"]
    assert report["lockProvenance"] == "lock", "the committed pylock pins the pilot's resolution"
    source_root = Path(iniconfig["source_root"])
    # No vendoring and no path edits: the plain compile resolves the wheel
    # through the same multi-root path any PEP 723 consumer takes.
    cli.main(
        [
            "compile",
            str(PILOT2),
            "--skip-check",
            "--emit",
            "ir",
            "-o",
            str(tmp_path / "pilot.ir.json"),
            "--debug-dir",
            str(tmp_path / "dbg"),
        ]
    )
    return fence_profile(tmp_path / "dbg" / "ir.json", source_root), source_root


@needs_network
def test_iniconfig_pilot_fence_profile_is_within_the_ceiling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYSC_UV", raising=False)
    monkeypatch.setenv("PYSC_NO_TELEMETRY", "1")
    monkeypatch.setenv("PYSC_CACHE_DIR", str(tmp_path / "cache"))
    profile, _ = _pilot2_profile(tmp_path)
    assert profile["total"] <= PILOT2_FENCE_CEILING, profile["fences"]
    # The bare `Final` constant and the optional-field tuple returns lower.
    assert "PS1103" not in profile["byCode"], profile["fences"]
    assert "PS1003" not in profile["byCode"], profile["fences"]


@needs_network
@needs_toolchain
@pytest.mark.xfail(
    strict=True,
    reason="#417 blockers: tuple dict keys #1368, exception init and fields #448, NamedTuple #1369, "
    "None-initialised locals #1370, __getitem__/__contains__ dispatch #446",
)
def test_iniconfig_pilot_matches_cpython(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYSC_UV", raising=False)
    monkeypatch.setenv("PYSC_NO_TELEMETRY", "1")
    monkeypatch.setenv("PYSC_CACHE_DIR", str(tmp_path / "cache"))
    profile, source_root = _pilot2_profile(tmp_path)
    assert profile["total"] == 0, profile["fences"]

    env = {**os.environ, "PYTHONPATH": str(source_root)}
    reference = subprocess.run(  # noqa: S603
        [sys.executable, str(PILOT2)], capture_output=True, text=True, env=env, timeout=60, check=False
    )
    assert reference.returncode == 0, reference.stderr
    binary = tmp_path / "iniconfig-pilot"
    assert cli.main(["compile", str(PILOT2), "--skip-check", "--emit", "bin", "-o", str(binary)]) == 0
    probe = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    assert probe.stdout == reference.stdout, (probe.stdout, reference.stdout, probe.stderr)
    assert probe.returncode == reference.returncode
