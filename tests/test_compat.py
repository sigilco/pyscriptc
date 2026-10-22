"""The compat denominators stay frozen. The full compile score is a separate command."""

from __future__ import annotations

from pathlib import Path

import pytest
from benchmarks.compat.score import classify_tree, self_check


def test_compat_denominators_match_this_interpreter() -> None:
    assert self_check() == []


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_a_package_counts_only_when_its_function_matches(tmp_path: Path) -> None:
    root = tmp_path / "sample"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "util.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    counted = classify_tree("sample", root)
    assert counted["status"] == "subset"
    assert counted["detail"].startswith("called sample.util.add")

    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "__init__.py").write_text("VERSION: str = 'v1'\n", encoding="utf-8")
    constants = classify_tree("bare", bare)
    assert constants["status"] == "partial"
    assert "no function was called" in constants["detail"]

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "__init__.py").write_text("import argparse\n", encoding="utf-8")
    refused = classify_tree("broken", broken)
    assert refused["status"] == "fenced"
    assert refused["detail"].startswith("PS")
    assert "argparse" in refused["detail"]

    script = tmp_path / "scripted"
    script.mkdir()
    (script / "__init__.py").write_text("", encoding="utf-8")
    (script / "util.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    (script / "__main__.py").write_text("import argparse\n", encoding="utf-8")
    skipped = classify_tree("scripted", script)
    assert skipped["status"] == "subset"
    assert skipped["detail"].startswith("called scripted.util.add")
