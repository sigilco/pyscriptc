"""Escape-hatch contract for the MIR lane (handoff 6, B12-prep).

Pins today's default (MIR off), both env switches, and the single reader of
the default. No behavior change: the flip is `MIR_DEFAULT = True` later.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyscriptc.miremit import MIR_DEFAULT, MIR_ENV, MIR_OFF, MIR_ON, mir_opt_in

ROOT = Path(__file__).resolve().parents[1]
MIREMIT = ROOT / "src" / "pyscriptc" / "miremit.py"
CLI_MAIN = ROOT / "src" / "pyscriptc" / "cli" / "_main.py"
DOC = ROOT / ".agents" / "docs" / "mir-escape-hatch.md"


def test_named_env_values() -> None:
    assert MIR_ENV == "PYSC_MIR"
    assert MIR_ON == "1"
    assert MIR_OFF == "0"


def test_today_default_is_off() -> None:
    """The inverted default for the flip is still False; unset means direct emit."""
    assert MIR_DEFAULT is False


def test_unset_follows_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MIR_ENV, raising=False)
    assert mir_opt_in() is MIR_DEFAULT
    assert mir_opt_in() is False


def test_empty_follows_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MIR_ENV, "")
    assert mir_opt_in() is MIR_DEFAULT
    assert mir_opt_in() is False


def test_off_switch_forces_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MIR_ENV, MIR_OFF)
    assert mir_opt_in() is False


def test_on_switch_forces_mir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MIR_ENV, MIR_ON)
    assert mir_opt_in() is True


def test_other_nonempty_value_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MIR_ENV, "yes")
    assert mir_opt_in() is True


def test_default_is_read_only_through_mir_opt_in() -> None:
    """Callers must not re-parse PYSC_MIR; the CLI goes through mir_opt_in()."""
    cli = CLI_MAIN.read_text(encoding="utf-8")
    assert "from pyscriptc.miremit import emit_llvm_mir, mir_opt_in" in cli
    assert "if mir_opt_in():" in cli
    assert 'os.environ.get("PYSC_MIR"' not in cli
    assert "os.environ.get('PYSC_MIR'" not in cli
    miremit = MIREMIT.read_text(encoding="utf-8")
    assert "def mir_opt_in()" in miremit
    assert "MIR_DEFAULT" in miremit


def test_contract_doc_names_the_three_values() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "PYSC_MIR" in text
    assert MIR_ON in text
    assert MIR_OFF in text
    assert "MIR_DEFAULT" in text
    assert "cli/_main.py" in text
    assert "mir_opt_in()" in text
