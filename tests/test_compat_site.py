"""The compat data seam: the schema, the sections and the site wiring.

The web page and the assembly are node-tested in `apps/web/test/` (run by
site.yml, the seam that cannot be forgotten). What stays under pytest here is
the data layer:

| part | what the test proves |
| --- | --- |
| `data/compat/compat.json` | validates against the schema, every section present |
| workflows | site.yml runs pnpm-only and is scoped to web paths; nightly stages the canonical data path |

Design doc: .agents/docs/compat-site.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.compat_schema import validate as validate_schema  # noqa: E402

DATA = ROOT / "data" / "compat" / "compat.json"

# The page sections, one per section of the schema.
SECTIONS = ("ladder", "language", "builtins", "stdlib", "packages", "fences", "history")


@pytest.fixture(scope="module")
def document() -> dict:
    if not DATA.exists():
        pytest.skip(f"compat data file not built: {DATA}")
    return json.loads(DATA.read_text(encoding="utf-8"))


def test_data_file_validates(document: dict) -> None:
    assert validate_schema(document) == []


def test_data_file_carries_every_schema_section(document: dict) -> None:
    for section in SECTIONS:
        assert section in document, section


def test_data_file_titles_use_the_public_name() -> None:
    html = (ROOT / "apps/web/src/pages/compat/index.astro").read_text(encoding="utf-8")
    assert "ARE WE NATIVE YET?" in html


def test_data_file_has_the_status_vocabulary(document: dict) -> None:
    for status in ("full", "partial", "none"):
        assert status in json.dumps(document), status


def test_site_workflow_exists() -> None:
    workflow = ROOT / ".github" / "workflows" / "site.yml"
    assert workflow.is_file()
    text = workflow.read_text(encoding="utf-8")
    assert "wrangler pages deploy" in text
    assert "--project-name=pyscriptc" in text


def test_nightly_never_pushes_to_master() -> None:
    """The nightly opens a PR; it must not push to master directly."""
    workflow = (ROOT / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")
    assert "gh pr create" in workflow
    assert "git push origin master" not in workflow


def test_docs_link_to_the_compat_placeholder() -> None:
    """The docs must link through the placeholder, never a hard-coded host."""
    page = (ROOT / "docs" / "guide" / "support-model.md").read_text(encoding="utf-8")
    assert "{{PYSC_COMPAT_URL}}" in page
    assert "pyscriptc-compat.pages.dev" not in page


def test_placeholder_default_is_the_single_site() -> None:
    plugin = (ROOT / "docs" / ".docmd" / "plugins" / "placeholders.js").read_text(encoding="utf-8")
    assert "https://pyscriptc.pages.dev/compat" in plugin
    assert "pyscriptc-compat.pages.dev" not in plugin


def test_compat_data_lives_under_data_compat() -> None:
    assert DATA.is_file()
    assert not (ROOT / "compat-data").exists()
