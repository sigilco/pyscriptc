"""First-class script bar documentation and link verification test.

Verifies that docs/reference/first-class-scripts.md exists, follows the reference
indexing, covers the required first-class criteria, specifies the falsifiable
corpus metrics (R0 and R1), documents the verification commands, and links to
the companion failure and divergence documentation.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "reference" / "first-class-scripts.md"
DOCMD_CONFIG = ROOT / "docs" / ".docmd" / "docmd.config.js"


def test_first_class_scripts_page_exists() -> None:
    assert PAGE.is_file(), f"missing documentation page: {PAGE}"


def test_page_indexed_in_docmd_config() -> None:
    assert DOCMD_CONFIG.is_file()
    config_content = DOCMD_CONFIG.read_text(encoding="utf-8")
    assert "/reference/first-class-scripts/" in config_content
    assert "First-class scripts" in config_content


def test_no_emdashes_in_page() -> None:
    content = PAGE.read_text(encoding="utf-8")
    assert "\u2014" not in content, "page contains emdash character"
    assert "\u2013" not in content, "page contains endash character"


def test_bar_criteria_present() -> None:
    content = PAGE.read_text(encoding="utf-8")
    # 5 criteria of the first-class bar
    assert "It compiles without workarounds" in content or "It compiles" in content
    assert "It matches CPython" in content
    assert "Diagnostics name the exact gap" in content or "Diagnostics name" in content
    assert "install and build story is one command" in content or "install story is one command" in content
    assert "Performance is predictable" in content or "performance is not a surprise" in content


def test_typed_subset_positioning_present() -> None:
    content = PAGE.read_text(encoding="utf-8")
    assert "Island is the floor" in content or "island is the floor" in content
    assert "Native is the ceiling" in content or "native is the ceiling" in content
    assert "typed subset" in content.lower()
    assert "no compiler shims for untyped code" in content.lower()


def test_verification_commands_present() -> None:
    content = PAGE.read_text(encoding="utf-8")
    assert "pyscriptc assess" in content
    assert "pyscriptc verify" in content
    assert "pyscriptc imports" in content


def test_corpus_metrics_and_commits_present() -> None:
    content = PAGE.read_text(encoding="utf-8")
    # R0 corpus metrics
    assert "39" in content
    assert "5860541e" in content
    assert "386" in content

    # R1 corpus metrics
    assert "45" in content
    assert "b42a7379" in content
    assert "116" in content


def test_companion_links_exist() -> None:
    content = PAGE.read_text(encoding="utf-8")
    links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", content)
    assert len(links) > 0

    for _, target in links:
        if target.startswith("{{") or target.startswith("http") or target.startswith("#"):
            continue
        target_path = target.split("#")[0]
        resolved = (PAGE.parent / target_path).resolve()
        assert resolved.exists(), f"broken link target in first-class-scripts.md: {target} (resolved to {resolved})"
