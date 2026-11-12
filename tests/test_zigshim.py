"""The `pip install ziglang` PATH shim: resolution, installation, doctor view."""

from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from pyscriptc import zigshim


@pytest.fixture()
def shim_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the shim directory and HOME into the test sandbox."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)  # type: ignore[method-assign]
    monkeypatch.setenv("PATH", os.defpath)
    return tmp_path / ".cache" / "pyscriptc" / "shim"


@pytest.fixture()
def fake_ziglang(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stub `ziglang` package that `python -m ziglang` answers like zig does."""
    pkg = tmp_path / "ziglang"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__main__.py").write_text(
        "import sys\n"
        "if sys.argv[1:] == ['version']:\n"
        "    print('0.17.0')\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit('stub')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    return pkg


def test_no_zig_anywhere_resolves_to_none(shim_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zigshim, "find_ziglang_interpreter", lambda: None)
    assert zigshim.resolve_zig() is None
    assert zigshim.ensure_on_path() is False


def test_wheel_only_install_gets_a_working_shim(shim_home: Path, fake_ziglang: Path) -> None:
    """No `zig` on PATH, but `ziglang` is importable: the shim takes over."""
    assert shutil_which("zig") is None
    resolved = zigshim.resolve_zig()
    assert resolved is not None
    script = Path(resolved)
    assert script.parent == zigshim.shim_dir()
    assert os.access(script, os.X_OK)
    got = subprocess.run([str(script), "version"], capture_output=True, text=True)
    assert got.returncode == 0
    assert got.stdout.strip() == "0.17.0"


def test_ensure_on_path_prepends_shim_dir(shim_home: Path, fake_ziglang: Path) -> None:
    directory = str(zigshim.shim_dir())
    assert zigshim.ensure_on_path() is True
    assert os.environ["PATH"].split(os.pathsep)[0] == directory
    assert shutil_which("zig") is not None


def test_is_shim_distinguishes_the_shim_from_a_real_zig(shim_home: Path, fake_ziglang: Path) -> None:
    assert zigshim.is_shim(str(zigshim.install_shim(sys.executable)))
    assert not zigshim.is_shim("/usr/bin/zig")
    assert not zigshim.is_shim(None)


def test_install_hint_names_the_wheel() -> None:
    hint = zigshim.install_hint()
    assert "pip install ziglang" in hint


def test_on_windows_the_wheel_exe_resolves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows resolves the wheel's own `zig.exe`, not a shell shim.

    The POSIX shim scripts are not subprocess-safe on Windows, so the
    compiler's own calls resolve the wheel's Win32 executable directly.
    """
    package = tmp_path / "ziglang"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    exe = package / "zig.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setitem(sys.modules, "ziglang", _fake_module(package))
    monkeypatch.setattr(zigshim.sys, "platform", "win32")
    monkeypatch.setattr(zigshim.shutil, "which", lambda name: None)
    assert zigshim.resolve_zig() == str(exe)
    assert zigshim.is_wheel_zig(str(exe))
    assert not zigshim.is_shim(str(exe))


def test_on_windows_the_shim_scripts_are_still_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The `.bat` and `zig` scripts stay for interactive shells."""
    monkeypatch.setattr(zigshim.sys, "platform", "win32")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    script = zigshim.install_shim(sys.executable)
    assert script.exists()
    bat = script.parent / "zig.bat"
    assert bat.exists()
    text = bat.read_text(encoding="utf-8")
    assert "-m ziglang" in text
    assert text.startswith("@")


def test_the_wheel_exe_is_not_confused_with_a_system_zig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A `zig.exe` outside the wheel package is not a wheel binary."""
    other = tmp_path / "zig.exe"
    other.write_bytes(b"MZ")
    package = tmp_path / "ziglang"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "ziglang", _fake_module(package))
    assert not zigshim.is_wheel_zig(str(other))
    assert not zigshim.is_wheel_zig(None)


def _fake_module(package: Path) -> object:
    """A stand-in `ziglang` module whose `__file__` points at the package."""
    module = types.ModuleType("ziglang")
    module.__file__ = str(package / "__init__.py")
    return module


def shutil_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def test_stale_shim_is_not_trusted(shim_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A shim whose interpreter is gone is rebuilt, not execed blind."""
    script = zigshim.shim_dir() / "zig"
    zigshim.shim_dir().mkdir(parents=True)
    script.write_text('#!/bin/sh\nexec "/gone/venv/bin/python" -m ziglang "$@"\n', encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setattr(zigshim, "find_ziglang_interpreter", lambda: None)
    assert zigshim.resolve_zig() is None
    assert zigshim.ensure_on_path() is False
    assert not script.exists()
