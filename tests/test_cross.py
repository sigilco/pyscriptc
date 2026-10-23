"""Cross-compilation plumbing: --driver/--target rules and the zig link path."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import pytest
from tests.test_spike import ROOT

from pyscriptc.cli import _apply_env_defaults, _require_toolchain, _resolve_default_driver
from pyscriptc.errors import IRError
from pyscriptc.llvm import link_binary

HELLO_LL = ROOT / "packaging" / "zig" / "hello.ll"


def _is_pe(path: Path) -> bool:
    data = path.read_bytes()
    if len(data) < 0x40 or data[:2] != b"MZ":
        return False
    pe_offset = int.from_bytes(data[0x3C:0x40], "little")
    return len(data) >= pe_offset + 4 and data[pe_offset : pe_offset + 4] == b"PE\0\0"


@pytest.mark.skipif(shutil.which("zig") is None, reason="zig not on PATH")
def test_cross_compile_windows_x86_64(tmp_path: Path) -> None:
    out = tmp_path / "hello_x64.exe"
    link_binary(HELLO_LL, out, driver="zig", target="x86_64-windows-gnu")
    assert out.is_file()
    assert _is_pe(out)


@pytest.mark.skipif(shutil.which("zig") is None, reason="zig not on PATH")
def test_cross_compile_windows_aarch64(tmp_path: Path) -> None:
    out = tmp_path / "hello_arm64.exe"
    link_binary(HELLO_LL, out, driver="zig", target="aarch64-windows-gnu")
    assert out.is_file()
    assert _is_pe(out)


@pytest.mark.skipif(shutil.which("zig") is None, reason="zig not on PATH")
def test_cli_windows_target_defaults_to_exe_suffix(tmp_path: Path) -> None:
    from pyscriptc.cli import main

    src = tmp_path / "test.py"
    src.write_text("print(42)\n")
    out = tmp_path / "myprog"
    ret = main(
        ["compile", str(src), "--emit", "bin", "-o", str(out), "--target", "x86_64-windows-gnu", "--driver", "zig"]
    )
    assert ret == 0
    exe = tmp_path / "myprog.exe"
    assert exe.is_file()
    assert _is_pe(exe)


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
@pytest.mark.skipif(sys.platform != "linux", reason="running Linux ELF binaries requires Linux")
def test_zig_target_musl_builds(tmp_path: Path) -> None:
    out = tmp_path / "hello-musl"
    link_binary(HELLO_LL, out, driver="zig", target="x86_64-linux-musl")
    import subprocess

    got = subprocess.run([str(out)], capture_output=True, text=True)
    assert got.stdout == "42\n"


def test_default_driver_prefers_zig_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(cmd: str) -> str | None:
        if cmd in ("zig", "clang"):
            return f"/usr/bin/{cmd}"
        return None

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert _resolve_default_driver() == "zig"


def test_default_driver_falls_back_to_clang_when_zig_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(cmd: str) -> str | None:
        if cmd == "clang":
            return "/usr/bin/clang"
        return None

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert _resolve_default_driver() == "clang"


def test_default_driver_returns_none_when_neither_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    monkeypatch.setattr(sys, "platform", "linux")
    assert _resolve_default_driver() is None


def test_default_driver_prefers_zig_when_target_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    monkeypatch.setattr(sys, "platform", "linux")
    assert _resolve_default_driver(target="x86_64-windows") == "zig"
    assert _resolve_default_driver(target="aarch64-linux-gnu.2.39") == "zig"


def test_default_driver_prefers_zig_on_win32(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    monkeypatch.setattr(sys, "platform", "win32")
    assert _resolve_default_driver() == "zig"


def test_apply_env_defaults_resolution_hierarchy(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(cmd: str) -> str | None:
        return f"/usr/bin/{cmd}" if cmd in ("zig", "clang") else None

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("PYSC_CC", raising=False)
    monkeypatch.delenv("PYSC_TARGET", raising=False)
    monkeypatch.delenv("PYSC_LINKER", raising=False)

    # 1. Default with zig on PATH: resolves to zig
    args = argparse.Namespace(driver=None, linker=None, target=None)
    _apply_env_defaults(args)
    assert args.driver == "zig"

    # 2. PYSC_CC env var overrides default
    monkeypatch.setenv("PYSC_CC", "clang")
    args = argparse.Namespace(driver=None, linker=None, target=None)
    _apply_env_defaults(args)
    assert args.driver == "clang"

    # 3. Explicit --driver flag wins over PYSC_CC
    args = argparse.Namespace(driver="zig", linker=None, target=None)
    _apply_env_defaults(args)
    assert args.driver == "zig"

    # 4. Target from PYSC_TARGET sets target and prefers zig
    monkeypatch.delenv("PYSC_CC", raising=False)
    monkeypatch.setenv("PYSC_TARGET", "x86_64-windows")
    args = argparse.Namespace(driver=None, linker=None, target=None)
    _apply_env_defaults(args)
    assert args.target == "x86_64-windows"
    assert args.driver == "zig"


def test_default_driver_prefers_clang_when_sanitizer_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    """`zig cc` compiles with -fsanitize= but ships no linkable ASan runtime.

    A sanitizer build must therefore keep the clang default instead of the
    zig preference, or the link dies with `undefined symbol: ___asan_init`.
    """

    def fake_which(cmd: str) -> str | None:
        return f"/usr/bin/{cmd}" if cmd in ("zig", "clang") else None

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert _resolve_default_driver(sanitize=["address"]) == "clang"
    assert _resolve_default_driver(sanitize=["address", "undefined"]) == "clang"
    assert _resolve_default_driver(sanitize=[]) == "zig"


def test_apply_env_defaults_sanitize_overrides_zig_preference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda cmd: f"/usr/bin/{cmd}" if cmd in ("zig", "clang") else None)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("PYSC_CC", raising=False)
    monkeypatch.delenv("PYSC_TARGET", raising=False)
    monkeypatch.delenv("PYSC_SANITIZE", raising=False)

    args = argparse.Namespace(driver=None, linker=None, target=None, sanitize="address")
    _apply_env_defaults(args)
    assert args.driver == "clang"

    args = argparse.Namespace(driver=None, linker=None, target=None, sanitize=None)
    _apply_env_defaults(args)
    assert args.driver == "zig"


def test_require_toolchain_diagnostics_mention_ziglang(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)

    # Neither toolchain found
    args_none = argparse.Namespace(run=True, emit="bin", driver=None)
    with pytest.raises(IRError) as excinfo:
        _require_toolchain(args_none)
    msg = str(excinfo.value)
    assert "no C toolchain found on PATH" in msg
    assert "neither zig nor clang" in msg
    assert "pip install ziglang" in msg
    assert "winget install zig" in msg
    assert "brew install zig" in msg
    assert "pyscriptc doctor" in msg

    # Explicit zig driver missing
    args_zig = argparse.Namespace(run=True, emit="bin", driver="zig")
    with pytest.raises(IRError) as excinfo_zig:
        _require_toolchain(args_zig)
    msg_zig = str(excinfo_zig.value)
    assert "zig not found on PATH" in msg_zig
    assert "pip install ziglang" in msg_zig
