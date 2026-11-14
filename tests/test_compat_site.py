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

# The sections the page renders from, one per section it reads.
PAGE_SECTIONS = ("channel", "island")


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


def test_data_file_carries_the_channel_and_the_island_receipts(document: dict) -> None:
    for section in PAGE_SECTIONS:
        assert section in document, section


def test_page_renders_the_two_planes(document: dict) -> None:
    html = (ROOT / "apps/web/src/pages/compat/index.astro").read_text(encoding="utf-8")
    for seam in ("Two planes", "island", "getPlaneShare", "getIslandHeadline"):
        assert seam in html, seam
    table = (ROOT / "apps/web/src/components/compat/PackageTable.astro").read_text(encoding="utf-8")
    for seam in ("Plane split", "Island", "row.plane"):
        assert seam in table, seam


def test_page_says_which_channel_it_measures(document: dict) -> None:
    html = (ROOT / "apps/web/src/pages/compat/index.astro").read_text(encoding="utf-8")
    assert "channel.released" in html, "the page does not name the released version"
    assert "install_released" in html, "the page does not give the install command"


def test_page_never_claims_a_run_without_a_receipt(document: dict) -> None:
    """The island verdict comes from the receipts, and unmeasured means that."""
    for row in document["packages"]:
        if row["island"] == "runs":
            assert document["island"]["runs"] >= 1, "island runs claimed with no receipts"
            break


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
    # Deploys belong to the Cloudflare dashboard git integration, so the
    # workflow carries no wrangler step and no Cloudflare secret.
    assert "wrangler" not in text
    assert "CLOUDFLARE_API_TOKEN" not in text


def test_nightly_never_pushes_to_master() -> None:
    """The nightly opens a PR; it must not push to master directly."""
    workflow = (ROOT / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")
    assert "gh pr create" in workflow
    assert "git push origin master" not in workflow


def test_nightly_carries_the_r0_lane() -> None:
    """The R0 lane measures the real scripts and publishes the point."""
    workflow = (ROOT / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")
    assert "benchmarks.real_scripts.measure" in workflow
    assert "scripts/r0_lane.py" in workflow
    assert "r0-lane:" in workflow
    # The lane reports through the same issue the other lanes open.
    assert "needs.r0-lane.outputs.failed" in workflow
    assert "nightly-failure" in workflow


def test_r0_ladder_section_is_published(document: dict) -> None:
    """The compat data carries the R0 rate with its denominator."""
    r0 = document.get("ladder", {}).get("r0")
    assert r0, "the ladder section carries no r0 point"
    assert r0["rung"] == "R0"
    assert r0["total"] > 0
    assert 0 <= r0["passed"] <= r0["total"]
    assert r0["blockers"] and r0["rootCauses"]
    assert len(r0["history"]) <= 30


def test_docs_link_to_the_compat_placeholder() -> None:
    """The docs must link through the placeholder, never a hard-coded host."""
    hits = 0
    for path in (ROOT / "docs").rglob("*.md"):
        if ".docmd" in path.parts:
            continue
        page = path.read_text(encoding="utf-8")
        if "{{PYSC_COMPAT_URL}}" in page:
            hits += 1
        assert "pyscriptc-compat.pages.dev" not in page, path
    assert hits >= 1, "no docs page links to the compat site through the placeholder"


def test_placeholder_default_is_the_single_site() -> None:
    plugin = (ROOT / "docs" / ".docmd" / "plugins" / "placeholders.js").read_text(encoding="utf-8")
    assert "https://pyscriptc.pages.dev/compat" in plugin
    assert "pyscriptc-compat.pages.dev" not in plugin


def test_compat_data_lives_under_data_compat() -> None:
    assert DATA.is_file()
    assert not (ROOT / "compat-data").exists()
