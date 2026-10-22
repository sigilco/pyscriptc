"""Support docs drift test: scripts/support_data.toml against the generated pages.

The generated pages are developer data, not user docs: they live under
`.agents/docs/support/` and the user-facing summary links to the compat site.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.gen_support_table import ROOT, build  # noqa: E402

SUPPORTED = ROOT / ".agents" / "docs" / "support" / "supported.md"
TOP100 = ROOT / ".agents" / "docs" / "support" / "compat-top100.md"


def test_build_has_no_errors() -> None:
    _, errors = build()
    assert errors == []


def test_generated_docs_are_current() -> None:
    files, errors = build()
    assert errors == []
    for path in (SUPPORTED, TOP100):
        assert path.read_text(encoding="utf-8") == files[str(path)]
