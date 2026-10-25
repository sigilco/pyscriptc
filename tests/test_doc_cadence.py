"""Test for doc cadence guard script."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.check_doc_cadence import check_markdown_file, scan_docs  # noqa: E402


def test_docs_are_cadence_clean() -> None:
    violations = scan_docs([ROOT / "docs", ROOT / ".agents" / "docs" / "support"])
    assert violations == [], "Found volatile metrics in docs:\n" + "\n".join(violations)


def test_cli_runner() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_doc_cadence.py")],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, f"check_doc_cadence failed:\n{result.stderr}\n{result.stdout}"


def test_catches_volatile_metric(tmp_path: Path) -> None:
    bad_md = tmp_path / "bad.md"
    bad_md.write_text("Total fences: 123 across the run\n", encoding="utf-8")
    violations = check_markdown_file(bad_md)
    assert len(violations) > 0
    assert "hardcoded total fences" in violations[0]
