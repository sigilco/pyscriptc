"""The vendored typeshed patch (`_patched_typeshed`) still realigns builtins under pyrefly."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pyscriptc import check
from pyscriptc.check import CheckFailed, _patched_typeshed, _patched_typeshed_cached, run_checker

needs_pyrefly = pytest.mark.skipif(shutil.which("pyrefly") is None, reason="pyrefly binary not on PATH")


def _cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "cache"
    monkeypatch.setenv("PYSC_TYPESHED_CACHE_DIR", str(root))
    monkeypatch.delenv("PYSC_NO_TYPESHED_CACHE", raising=False)
    return root


def _build_spy(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    calls: list[Path] = []
    real = check._patched_typeshed

    def spy(dest: Path) -> Path:
        calls.append(dest)
        return real(dest)

    monkeypatch.setattr(check, "_patched_typeshed", spy)
    return calls


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


def test_typeshed_cache_miss_builds_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _cache_env(tmp_path, monkeypatch)
    fallback = tmp_path / "tmp" / "typeshed"
    out = _patched_typeshed_cached(fallback)
    assert out.parent == root
    assert (out / ".pysc-complete").is_file()
    text = (out / "stdlib" / "builtins.pyi").read_text(encoding="utf-8")
    assert "class frozenset(set[_T_co]):" in text
    assert not fallback.exists()


def test_typeshed_cache_hit_reuses_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _cache_env(tmp_path, monkeypatch)
    calls = _build_spy(monkeypatch)
    first = _patched_typeshed_cached(tmp_path / "a" / "typeshed")
    second = _patched_typeshed_cached(tmp_path / "b" / "typeshed")
    assert first == second
    assert len(calls) == 1


def test_typeshed_cache_corrupt_entry_rebuilt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _cache_env(tmp_path, monkeypatch)
    calls = _build_spy(monkeypatch)
    first = _patched_typeshed_cached(tmp_path / "a" / "typeshed")
    builtins = first / "stdlib" / "builtins.pyi"
    builtins.write_text("corrupt\n", "utf-8")
    second = _patched_typeshed_cached(tmp_path / "b" / "typeshed")
    assert second == first
    assert len(calls) == 2
    assert "class frozenset(set[_T_co]):" in builtins.read_text(encoding="utf-8")


def test_typeshed_cache_disabled_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _cache_env(tmp_path, monkeypatch)
    monkeypatch.setenv("PYSC_NO_TYPESHED_CACHE", "1")
    fallback = tmp_path / "tmp" / "typeshed"
    out = _patched_typeshed_cached(fallback)
    assert out == fallback
    assert (out / "stdlib" / "builtins.pyi").is_file()


@needs_pyrefly
def test_unpatched_contract_still_rejected(tmp_path: Path) -> None:
    source = tmp_path / "bad.py"
    source.write_text("n: int = 'x'\n", encoding="utf-8")
    with pytest.raises(CheckFailed):
        run_checker(source, "pyrefly")
