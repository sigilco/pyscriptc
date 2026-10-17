"""Audit logger: JSONL records, level gating, and the `logs` subcommand."""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc import log as pysc_log
from pyscriptc.log import get_logger, log_dir, log_path

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolate_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PYSC_LOG_DIR", str(tmp_path / "audit"))
    monkeypatch.delenv("PYSC_DEBUG", raising=False)
    return tmp_path / "audit"


def _records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_info_writes_jsonl(_isolate_log_dir: Path) -> None:
    get_logger().info("compile.start", source="x.py", fences=2)
    path = log_path()
    assert path.parent == _isolate_log_dir
    (rec,) = _records(path)
    assert rec["event"] == "compile.start"
    assert rec["level"] == "info"
    assert rec["source"] == "x.py"
    assert rec["fences"] == 2
    for key in ("ts", "pysc", "py", "pid"):
        assert key in rec


def test_debug_gated_by_default(_isolate_log_dir: Path) -> None:
    get_logger().debug("quiet.event")
    assert not log_path().exists()


def test_debug_enabled_via_env(_isolate_log_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYSC_DEBUG", "1")
    get_logger().debug("quiet.event", k="v")
    (rec,) = _records(log_path())
    assert rec["level"] == "debug"
    assert rec["k"] == "v"


def test_log_path_is_dated(_isolate_log_dir: Path) -> None:
    day = datetime.date(2030, 1, 2)
    assert log_path(day) == _isolate_log_dir / "20300102.jsonl"
    assert log_dir() == _isolate_log_dir


def test_logs_subcommand_prints_path_and_tail(_isolate_log_dir: Path) -> None:
    get_logger().info("marker.event", n=1)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYSC_LOG_DIR": str(_isolate_log_dir)}
    proc = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "logs"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert str(log_path()) in proc.stdout
    assert "marker.event" in proc.stdout


def test_tail_without_file(_isolate_log_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pysc_log.tail()
    out = capsys.readouterr().out
    assert str(log_path()) in out
    assert "no records yet" in out
