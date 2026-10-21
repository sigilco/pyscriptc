"""Checker binary resolution: running-interpreter scripts dir, then env, PATH, `-m` fallback."""

from __future__ import annotations

import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from pyscriptc import check
from pyscriptc.check import _resolve_pyrefly_binary, _resolve_tool_argv


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.delenv("PYREFLY_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    return monkeypatch


def _scripts_at(monkeypatch: pytest.MonkeyPatch, default: Path, user: Path | None = None) -> None:
    """Point sysconfig's scripts dirs at temp dirs; the user scheme falls back to an empty dir."""
    user_dir = user if user is not None else default.parent / "no-user-scripts"
    real_get_path = sysconfig.get_path

    def fake_get_path(name: str, scheme: str | None = None) -> str:
        if name != "scripts":
            return real_get_path(name)
        if scheme is None:
            return str(default)
        return str(user_dir)

    monkeypatch.setattr(sysconfig, "get_path", fake_get_path)
    monkeypatch.setattr(sysconfig, "get_preferred_scheme", lambda kind: "user_scheme")


def test_prefers_running_interpreter_scripts_dir(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    binary = _executable(tmp_path / "venv" / "bin" / "pyrefly")
    _scripts_at(clean_env, binary.parent)
    clean_env.setenv("PYREFLY_BIN", "/elsewhere/pyrefly")
    assert _resolve_pyrefly_binary() == [str(binary)]


def test_uses_exe_suffix_from_config(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    binary = _executable(tmp_path / "bin" / "pyrefly.exe")
    _scripts_at(clean_env, binary.parent)
    real_get_config_var = sysconfig.get_config_var
    clean_env.setattr(sysconfig, "get_config_var", lambda name: ".exe" if name == "EXE" else real_get_config_var(name))
    assert _resolve_pyrefly_binary() == [str(binary)]


def test_skips_non_executable_candidate(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    stub = tmp_path / "bin" / "pyrefly"
    stub.parent.mkdir()
    stub.write_text("not executable", encoding="utf-8")
    stub.chmod(0o644)
    _scripts_at(clean_env, stub.parent)
    clean_env.setenv("PYREFLY_BIN", "/override/pyrefly")
    assert _resolve_pyrefly_binary() == ["/override/pyrefly"]


def test_user_scheme_scripts_dir_is_second(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    user_binary = _executable(tmp_path / "user-bin" / "pyrefly")
    _scripts_at(clean_env, tmp_path / "empty-bin", user=user_binary.parent)
    clean_env.setenv("PYREFLY_BIN", "/override/pyrefly")
    assert _resolve_pyrefly_binary() == [str(user_binary)]


def test_env_override_splits_multi_token_value(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    _scripts_at(clean_env, tmp_path / "empty-bin")
    clean_env.setenv("PYREFLY_BIN", "uvx 'pyre fly' --flag")
    assert _resolve_pyrefly_binary() == ["uvx", "pyre fly", "--flag"]


def test_path_lookup_comes_before_fallback(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    _scripts_at(clean_env, tmp_path / "empty-bin")
    clean_env.setattr("shutil.which", lambda name: f"/opt/bin/{name}" if name == "pyrefly" else None)
    assert _resolve_pyrefly_binary() == ["/opt/bin/pyrefly"]


def test_final_fallback_is_interpreter_module(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    _scripts_at(clean_env, tmp_path / "empty-bin")
    assert _resolve_pyrefly_binary() == [sys.executable, "-m", "pyrefly"]


def test_generic_resolver_fallback_is_taken_verbatim(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    _scripts_at(clean_env, tmp_path / "empty-bin")
    assert _resolve_tool_argv("pyright", "PYRIGHT_BIN", ["pyright"]) == ["pyright"]


def test_run_pyrefly_invokes_multi_token_fallback(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    _scripts_at(clean_env, tmp_path / "empty-bin")
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    clean_env.setattr(check.subprocess, "run", fake_run)
    source = tmp_path / "ok.py"
    source.write_text("x = 1\n", encoding="utf-8")
    check._run_pyrefly(source)
    assert seen and seen[0][:4] == [sys.executable, "-m", "pyrefly", "check"]
    flag = seen[0].index("--python-interpreter-path")
    assert seen[0][flag + 1] == sys.executable


def test_run_pyrefly_sees_running_interpreter(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    """pyrefly must resolve site-packages from the interpreter running pyscriptc, not the host Python."""
    _scripts_at(clean_env, tmp_path / "empty-bin")
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    clean_env.setattr(check.subprocess, "run", fake_run)
    source = tmp_path / "ok.py"
    source.write_text("x = 1\n", encoding="utf-8")
    check._run_pyrefly(source)
    argv = seen[0]
    assert argv[argv.index("--python-interpreter-path") + 1] == sys.executable
    assert argv[-1] == str(source.resolve())
