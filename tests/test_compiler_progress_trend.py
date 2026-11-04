"""Unit tests for the compiler progress trend markdown."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("compiler_progress", ROOT / "scripts" / "compiler_progress.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fence_seams_buckets_modules(tmp_path: Path) -> None:
    pins = tmp_path / "pins.json"
    pins_map = {
        "pyscriptc.lower._core": 10,
        "pyscriptc.emit.llvm._core": 5,
        "pyscriptc.mir": 2,
        "pyscriptc.cli._main": 7,
        "pyscriptc.assess": 1,
    }
    pins.write_text(json.dumps(pins_map), encoding="utf-8")
    seams = _load().fence_seams(pins)
    assert seams == {"lower": 10, "emit": 5, "mir": 2, "cli": 7, "other": 1}


def test_trend_table_deltas_against_baseline(tmp_path: Path) -> None:
    mod = _load()
    pins = tmp_path / "pins.json"
    pins.write_text(json.dumps({"pyscriptc.lower._core": 10}), encoding="utf-8")
    current = mod.snapshot({"lower": {"lines": 100, "dict_build": 3, "dict_read": 4, "typed_use": 5}}, pins)
    baseline = {
        "totals": {"lower": {"lines": 90, "dict_build": 5, "dict_read": 4, "typed_use": 2}},
        "fences": {"lower": 12, "other": 0},
    }
    text = mod.markdown_trend(current, baseline, "baseline.json")
    assert "| lower.lines | 100 | 90 | +10 |" in text
    assert "| lower.dict_build | 3 | 5 | -2 |" in text
    assert "| lower | 10 | 12 | -2 |" in text
    assert "Baseline: baseline.json" in text


def test_trend_table_without_baseline_shows_zero_baseline() -> None:
    mod = _load()
    text = mod.markdown_trend({"totals": {}, "fences": {"lower": 3}}, None, "origin/master~20")
    assert "Baseline: origin/master~20" in text
    assert "| lower | 3 | 0 | +3 |" in text
