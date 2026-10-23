"""Tests for benchmarks/compat2/version_surface.py (C23 version-track survey)."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.compat2 import version_surface


def test_report_is_stable_across_runs() -> None:
    first = version_surface.emit(version_surface.build_report())
    second = version_surface.emit(version_surface.build_report())
    assert first == second
    report = json.loads(first)
    assert report["scan"]["examples"]["files"] == 29
    assert report["scan"]["examples"]["scripts"] == 5
    assert report["scan"]["examples"]["modules"] == 24
    assert report["scan"]["corpus_scripts"]["files"] == 20
    assert report["scan"]["total_files"] == 49
    assert report["versions"] == ["3.10", "3.11", "3.12", "3.13", "3.14"]


def test_known_feature_fixture_type_alias(tmp_path: Path) -> None:
    """A `type` statement gates a file to 3.12+ and is named exactly."""
    src = "type X = int\ny: X = 1\n"
    fixture = tmp_path / "alias.py"
    fixture.write_text(src, encoding="utf-8")

    rec = version_surface.scan_file(fixture, rel=Path("fixture/alias.py"))

    assert rec["min_version"] == "3.12"
    assert rec["parse"]["3.10"]["ok"] is False
    assert rec["parse"]["3.11"]["ok"] is False
    assert rec["parse"]["3.12"]["ok"] is True
    assert rec["gated_by"] == "type_alias"
    assert {f["feature"] for f in rec["features"]} == {"type_alias"}


def test_parse_ladder_catches_except_star(tmp_path: Path) -> None:
    src = "try:\n    pass\nexcept* ValueError:\n    pass\n"
    fixture = tmp_path / "star.py"
    fixture.write_text(src, encoding="utf-8")

    rec = version_surface.scan_file(fixture, rel=Path("fixture/star.py"))

    assert rec["min_version"] == "3.11"
    assert rec["parse"]["3.10"]["ok"] is False
    assert rec["gated_by"] == "except_star"
    assert {f["feature"] for f in rec["features"]} == {"except_star"}


def test_corpus_files_parse_at_3_10_or_report_feature() -> None:
    """Every file failing the 3.10 grammar but passing 3.12 names its feature."""
    report = version_surface.build_report()
    for rec in report["files"]:
        if rec["parse"]["3.10"]["ok"]:
            continue
        if not rec["parse"]["3.12"]["ok"]:
            continue
        assert rec["parse"]["3.10"]["feature"]
        assert rec["gated_by"] == rec["parse"]["3.10"]["feature"]
