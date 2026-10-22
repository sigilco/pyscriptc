from __future__ import annotations

import sys
from pathlib import Path

import pytest
from benchmarks.compat2 import cache
from benchmarks.compat2 import score as score_module
from benchmarks.compat2.score import pinned_oracle, resolve_oracle


def fake_interpreter(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def versions(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Pretend each fake executable reports the version recorded here."""

    table: dict[str, str] = {}

    def probe(executable: str) -> tuple[tuple[int, int], str]:
        full = table[executable]
        major, minor = full.split(".")[:2]
        return (int(major), int(minor)), full

    monkeypatch.setattr(score_module, "_probe_version", probe)
    monkeypatch.delenv("PYSC_ORACLE", raising=False)
    monkeypatch.delenv("PYSC_ORACLE_DIR", raising=False)
    return table


def test_env_executable_wins_over_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, versions: dict[str, str]
) -> None:
    env_exe = fake_interpreter(tmp_path / "env" / "python3")
    dir_exe = fake_interpreter(tmp_path / "dir" / "cpython-3.14.1-x" / "bin" / "python3")
    versions.update({env_exe: "3.14.2", dir_exe: "3.14.1"})
    monkeypatch.setenv("PYSC_ORACLE", env_exe)
    monkeypatch.setenv("PYSC_ORACLE_DIR", str(tmp_path / "dir"))
    assert resolve_oracle(None) == (env_exe, f"{cache.SCORER_VERSION}/cpython-3.14.2")


def test_dir_used_without_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, versions: dict[str, str]) -> None:
    exe = fake_interpreter(tmp_path / "cpython-3.14.1-x" / "bin" / "python3")
    versions[exe] = "3.14.1"
    monkeypatch.setenv("PYSC_ORACLE_DIR", str(tmp_path))
    assert pinned_oracle() == exe


def test_uv_find_used_last(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, versions: dict[str, str]) -> None:
    exe = fake_interpreter(tmp_path / "uv" / "python3.14")
    versions[exe] = "3.14.0"

    class Done:
        stdout = exe + "\n"

    calls: list[list[str]] = []

    def run(cmd: list[str], **_: object) -> Done:
        calls.append(cmd)
        return Done()

    monkeypatch.setattr(score_module.subprocess, "run", run)
    assert pinned_oracle() == exe
    assert calls == [["uv", "python", "find", "3.14"]]


def test_hard_fail_without_any_3_14(monkeypatch: pytest.MonkeyPatch, versions: dict[str, str]) -> None:
    def run(*_: object, **__: object) -> None:
        raise FileNotFoundError("uv")

    monkeypatch.setattr(score_module.subprocess, "run", run)
    monkeypatch.setattr(sys, "version_info", (3, 12, 1, "final", 0))
    with pytest.raises(SystemExit, match="CPython 3.14"):
        pinned_oracle()


def test_wrong_version_hard_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, versions: dict[str, str]) -> None:
    exe = fake_interpreter(tmp_path / "python3")
    versions[exe] = "3.12.4"
    monkeypatch.setenv("PYSC_ORACLE", exe)
    with pytest.raises(SystemExit, match="pinned to CPython 3.14"):
        pinned_oracle()


def test_non_executable_env_hard_fails(monkeypatch: pytest.MonkeyPatch, versions: dict[str, str]) -> None:
    monkeypatch.setenv("PYSC_ORACLE", "3.12")
    with pytest.raises(SystemExit, match="not an executable path"):
        pinned_oracle()
