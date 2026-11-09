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

    assert "| mir.percent | 0 | 0 | +0 |" in text


def test_trend_table_includes_mir_slice_coverage() -> None:
    mod = _load()
    current = {
        "totals": {},
        "fences": {},
        "mir_coverage": {"percent": 10, "in_slice": 3, "functions": 31, "script_count": 20},
    }
    baseline = {
        "totals": {},
        "fences": {},
        "mir_coverage": {"percent": 0, "in_slice": 0, "functions": 0, "script_count": 20},
    }
    text = mod.markdown_trend(current, baseline, "baseline.json")
    assert "### MIR slice coverage" in text
    assert "| mir.percent | 10 | 0 | +10 |" in text
    assert "| mir.in_slice | 3 | 0 | +3 |" in text
    assert "| mir.functions | 31 | 0 | +31 |" in text


def test_trend_table_includes_out_of_slice_reasons() -> None:
    mod = _load()
    current = {
        "totals": {},
        "fences": {},
        "mir_coverage": {"out_of_slice": 28, "reason_kinds": 12},
        "mir_reasons": {"local:list": 6, "expr:call": 4},
    }
    baseline = {
        "totals": {},
        "fences": {},
        "mir_coverage": {"out_of_slice": 30, "reason_kinds": 12},
        "mir_reasons": {"local:list": 6, "expr:call": 6},
    }
    text = mod.markdown_trend(current, baseline, "baseline.json")
    assert "| mir.out_of_slice | 28 | 30 | -2 |" in text
    assert "| mir.reason_kinds | 12 | 12 | +0 |" in text
    assert "### MIR out-of-slice reasons (top 10)" in text
    assert "| `expr:call` | 4 | 6 | -2 |" in text
    assert "| `local:list` | 6 | 6 | +0 |" in text
