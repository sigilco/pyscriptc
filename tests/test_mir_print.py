"""Goldens for the textual MIR printer behind `compile --emit mir`.

Regenerate a golden only when the printer or the MIR lowering of the
fixture moves on purpose:

    PYSC_BLESS_MIR_PRINT=1 uv run pytest tests/test_mir_print.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pyscriptc.cli import main as cli_main
from pyscriptc.lower import lower_file
from pyscriptc.mir import OP_ARITY, op_name
from pyscriptc.mirlower import lower_module
from pyscriptc.mirprint import print_module

_LEAF = Path(__file__).parent / "fixtures" / "mir_leaf"
_GOLDEN = Path(__file__).parent / "fixtures" / "mir_print"
# Discovery over the golden files: adding a .mir golden opts the matching
# leaf fixture into the printer sweep with no list edit.
GOLDEN_SAMPLES = tuple(sorted(p.stem for p in _GOLDEN.glob("*.mir")))


def _render(stem: str) -> str:
    module, _ = lower_file(str(_LEAF / f"{stem}.py"))
    return print_module(lower_module(module))


@pytest.mark.parametrize("stem", GOLDEN_SAMPLES)
def test_mir_text_matches_golden(stem: str) -> None:
    text = _render(stem)
    golden = _GOLDEN / f"{stem}.mir"
    if os.environ.get("PYSC_BLESS_MIR_PRINT"):
        golden.write_text(text, encoding="utf-8")
    assert text == golden.read_text(encoding="utf-8")


@pytest.mark.parametrize("stem", GOLDEN_SAMPLES)
def test_mir_text_is_stable_across_lowerings(stem: str) -> None:
    assert _render(stem) == _render(stem)


def test_every_op_has_a_mnemonic() -> None:
    for op in OP_ARITY:
        assert op_name(op) != f"op{op}"
    names = [op_name(op) for op in OP_ARITY]
    assert len(set(names)) == len(names)


def test_cli_emit_mir_writes_the_printer_text(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "leafint.py"
    src.write_text((_LEAF / "leafint.py").read_text(encoding="utf-8"), encoding="utf-8")
    out = tmp_path / "out.mir"
    rc = cli_main(["compile", str(src), "--emit", "mir", "-o", str(out)])
    assert rc == 0
    assert capsys.readouterr().out.strip() == str(out)
    assert out.read_text(encoding="utf-8") == (_GOLDEN / "leafint.mir").read_text(encoding="utf-8")
