"""The progress tracker writes a markdown trend table for the CI job summary."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("compiler_progress", ROOT / "scripts" / "compiler_progress.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_summary_lists_seams_and_top_fence_modules(tmp_path: Path) -> None:
    pins = tmp_path / "pins.json"
    pins.write_text(json.dumps({"pkg.a": 5, "pkg.b": 9, "pkg.c": 0}), encoding="utf-8")
    totals = {"lower": {"lines": 10, "dict_build": 2, "dict_read": 3, "typed_use": 4}}
    text = _load().summary_markdown(totals, pins)
    assert "| lower | 10 | 2 | 3 | 4 |" in text
    assert "Self-host fences: 14 across 3 modules" in text
    assert text.index("pkg.b") < text.index("pkg.a")


def test_summary_without_pins_still_has_the_seam_table(tmp_path: Path) -> None:
    totals = {"emit": {"lines": 1, "dict_build": 0, "dict_read": 1, "typed_use": 0}}
    text = _load().summary_markdown(totals, tmp_path / "missing.json")
    assert "| emit | 1 | 0 | 1 | 0 |" in text
    assert "Self-host fences" not in text
