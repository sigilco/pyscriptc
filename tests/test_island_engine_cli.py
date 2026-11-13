"""CLI wiring of `--island-engine` (issue #666): accepted, recorded, inert."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

ECHO = 'print("ok")\n'

DESCRIPTOR = """\
schema_version = 1
capabilities = ["fallback"]

[engine]
name = "pypy"
version = "7.3.16"
kind = "interpreter"
"""


@pytest.fixture
def descriptor(tmp_path: Path) -> Path:
    path = tmp_path / "pypy.toml"
    path.write_text(DESCRIPTOR, encoding="utf-8")
    return path


def test_flag_accepted_and_recorded_in_debug_summary(tmp_path: Path, descriptor: Path) -> None:
    source = write_case(tmp_path, "echo.py", ECHO)
    got = run_cli(
        "compile",
        str(source),
        "--emit",
        "ir",
        "--island-engine",
        str(descriptor),
        f"--debug-dir={tmp_path / 'dbg'}",
    )
    assert got.returncode == 0, got.stderr
    import json

    summary = json.loads((tmp_path / "dbg" / "summary.json").read_text(encoding="utf-8"))
    engine = summary["islandEngine"]
    assert engine["name"] == "pypy"
    assert engine["version"] == "7.3.16"
    assert engine["kind"] == "interpreter"
    assert engine["capabilities"] == ["fallback"]
    assert engine["source"] == str(descriptor)


def test_debug_line_prints_engine(tmp_path: Path, descriptor: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # In-process so capsys sees the [debug] stderr lines.

    from pyscriptc.cli import _main

    source = write_case(tmp_path, "echo.py", ECHO)
    argv = [
        "compile",
        str(source),
        "--emit",
        "ir",
        f"--island-engine={descriptor}",
        "--debug=stages",
    ]
    code = _main.main(argv)
    assert code == 0
    err = capsys.readouterr().err
    assert "[debug] island-engine: pypy 7.3.16 kind=interpreter capabilities=fallback" in err
    assert str(descriptor) in err


def test_default_path_unchanged_without_flag(tmp_path: Path, descriptor: Path) -> None:
    source = write_case(tmp_path, "echo.py", ECHO)
    got = run_cli("compile", str(source), "--emit", "ir", f"--debug-dir={tmp_path / 'dbg'}")
    assert got.returncode == 0, got.stderr
    import json

    summary = json.loads((tmp_path / "dbg" / "summary.json").read_text(encoding="utf-8"))
    assert "islandEngine" not in summary


def test_config_engine_key_selected(tmp_path: Path) -> None:
    source = write_case(tmp_path, "echo.py", ECHO)
    (tmp_path / "pyscriptc.toml").write_text('[pyscriptc.island]\nengine = "cpython"\n', encoding="utf-8")
    got = run_cli(
        "compile",
        str(source),
        "--emit",
        "ir",
        f"--debug-dir={tmp_path / 'dbg'}",
    )
    assert got.returncode == 0, got.stderr
    import json

    summary = json.loads((tmp_path / "dbg" / "summary.json").read_text(encoding="utf-8"))
    assert summary["islandEngine"]["name"] == "cpython"
    assert summary["islandEngine"]["source"] == "builtin"


def test_unknown_engine_is_a_clean_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYSC_ISLAND_ENGINES", raising=False)
    source = write_case(tmp_path, "echo.py", ECHO)
    got = run_cli("compile", str(source), "--emit", "ir", "--island-engine", "pypy")
    assert got.returncode != 0
    assert "unknown engine" in got.stderr
    assert "Traceback" not in got.stderr


def test_malformed_descriptor_is_a_clean_error(tmp_path: Path) -> None:
    source = write_case(tmp_path, "echo.py", ECHO)
    bad = tmp_path / "bad.toml"
    bad.write_text('schema_version = 1\n[engine]\nname = "x"\n', encoding="utf-8")
    got = run_cli("compile", str(source), "--emit", "ir", "--island-engine", str(bad))
    assert got.returncode != 0
    assert "engine descriptor" in got.stderr
    assert "Traceback" not in got.stderr
