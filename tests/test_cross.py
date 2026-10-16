"""Cross-compilation plumbing: --driver/--target rules and the zig link path."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from tests.test_spike import ROOT

from pyscriptc.errors import IRError
from pyscriptc.llvm import link_binary

HELLO_LL = ROOT / "packaging" / "zig" / "hello.ll"


def test_target_requires_zig_driver(tmp_path: Path) -> None:
    with pytest.raises(IRError, match="--target requires --driver zig"):
        link_binary(HELLO_LL, tmp_path / "h", driver="clang", target="aarch64-macos")


@pytest.mark.linux_container
@pytest.mark.skipif(shutil.which("zig") is None, reason="zig not on PATH")
def test_zig_driver_host_build_runs(tmp_path: Path) -> None:
    out = tmp_path / "hello-zig"
    link_binary(HELLO_LL, out, driver="zig")
    import subprocess

    got = subprocess.run([str(out)], capture_output=True, text=True)
    assert got.stdout == "42\n"


@pytest.mark.linux_container
@pytest.mark.skipif(shutil.which("zig") is None, reason="zig not on PATH")
def test_zig_target_musl_builds(tmp_path: Path) -> None:
    out = tmp_path / "hello-musl"
    link_binary(HELLO_LL, out, driver="zig", target="x86_64-linux-musl")
    import subprocess

    got = subprocess.run([str(out)], capture_output=True, text=True)
    assert got.stdout == "42\n"
