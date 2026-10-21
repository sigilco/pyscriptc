"""The vendored typeshed patch (`_patched_typeshed`) still realigns builtins under pyrefly."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pyscriptc.check import CheckFailed, _patched_typeshed, run_checker

needs_pyrefly = pytest.mark.skipif(shutil.which("pyrefly") is None, reason="pyrefly binary not on PATH")


def test_patch_applies_to_vendored_builtins(tmp_path: Path) -> None:
    text = (_patched_typeshed(tmp_path / "ts") / "stdlib" / "builtins.pyi").read_text(encoding="utf-8")
    assert "class frozenset(set[_T_co]):" in text
    start = text.index("class range(")
    assert "-> list[int]:" in text[start : text.index("\nclass ", start)]


@needs_pyrefly
def test_patches_hold_end_to_end(tmp_path: Path) -> None:
    source = tmp_path / "ok.py"
    source.write_text(
        "def f(xs: set[int]) -> None:\n    pass\n\n"
        "a: list[int] = range(3)\n"
        "f(frozenset([1]))\n"
        'parts: list[str] = "a b".split()\n',
        encoding="utf-8",
    )
    run_checker(source, "pyrefly")


@needs_pyrefly
def test_unpatched_contract_still_rejected(tmp_path: Path) -> None:
    source = tmp_path / "bad.py"
    source.write_text("n: int = 'x'\n", encoding="utf-8")
    with pytest.raises(CheckFailed):
        run_checker(source, "pyrefly")
