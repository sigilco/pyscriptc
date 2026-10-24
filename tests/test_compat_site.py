"""The site: the compat page, the assembly and the link check.

Three things are checked here, all of them offline and fast:

| part | what the test proves |
| --- | --- |
| `compat/build.py` | the page renders every section the schema defines, and the data file travels with it |
| `scripts/build_site.py` | the assembled `deploy/` tree has the three parts and the redirect rules |
| `scripts/check_site_links.py` | no link in the assembled tree points at a file that does not exist |

The tests build into a temp directory, so they never touch `deploy/` or
`dist/`.

Design doc: .agents/docs/compat-site.md
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from compat.build import PAGE_FILES
from compat.build import build as build_compat
from scripts.build_site import REQUIRED
from scripts.build_site import validate as validate_deploy
from scripts.check_site_links import check_deploy, check_sources
from scripts.compat_schema import validate as validate_schema
from scripts.compat_schema import validate_summary

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

COMPAT_PAGE = ROOT / "compat" / "site" / "index.html"
DATA = ROOT / "compat" / "data" / "compat.json"
DATA_SUMMARY = ROOT / "compat" / "data" / "summary.json"

# The page sections, one per section of the schema.
SECTIONS = ("language", "builtins", "stdlib", "pypi", "fences", "targets", "divergences", "history")


@pytest.fixture(scope="module")
def document() -> dict:
    if not DATA.exists():
        pytest.skip(f"compat data file not built: {DATA}")
    return json.loads(DATA.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The compat page, built into a temp directory."""
    out = tmp_path_factory.mktemp("compat")
    build_compat(out, DATA, "/compat/")
    return out


def test_data_file_validates(document: dict) -> None:
    assert validate_schema(document) == []


def test_page_reads_every_schema_section() -> None:
    """The page must render every section the data file carries."""
    html = COMPAT_PAGE.read_text(encoding="utf-8")
    for section in SECTIONS:
        assert f'id="{section}"' in html, section


def test_page_titles_are_the_public_name() -> None:
    html = COMPAT_PAGE.read_text(encoding="utf-8")
    assert "<title>What pyscriptc supports" in html
    assert "pyscriptc" in html


def test_page_has_the_status_vocabulary() -> None:
    html = COMPAT_PAGE.read_text(encoding="utf-8")
    for status in ("full", "partial", "none"):
        assert status in html, status


def test_build_copies_the_page_and_the_data(built: Path) -> None:
    for name in PAGE_FILES:
        assert (built / name).is_file(), name
    assert (built / "data" / "compat.json").is_file()


def test_build_bakes_the_base_path(built: Path) -> None:
    html = (built / "index.html").read_text(encoding="utf-8")
    assert "{{BASE}}" not in html
    assert "/compat/" in html


def test_build_copies_the_assets(built: Path) -> None:
    for name in ("logo-256.png", "icon-64.png"):
        assert (built / name).is_file(), name


def test_build_fails_without_data(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        build_compat(tmp_path / "out", tmp_path / "missing.json", "/compat/")


def test_assembly_requires_the_three_parts(built: Path) -> None:
    """The assembly validator names every part it expects to find."""
    assert "index.html" in REQUIRED
    assert "docs/index.html" in REQUIRED
    assert "compat/index.html" in REQUIRED
    assert "compat/data/compat.json" in REQUIRED


def test_assembly_validator_reports_a_missing_part(tmp_path: Path) -> None:
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    assert validate_deploy(deploy) == 1


def test_link_check_passes_on_the_page_source() -> None:
    problems = check_sources()
    assert problems == [], problems


def test_link_check_finds_a_broken_link(tmp_path: Path) -> None:
    deploy = tmp_path / "deploy"
    (deploy / "docs").mkdir(parents=True)
    (deploy / "index.html").write_text('<a href="/docs/missing.html">x</a>', encoding="utf-8")
    problems = check_deploy(deploy, check_external=False)
    assert problems, "the checker must report the broken link"


def test_link_check_passes_on_the_assembled_tree(tmp_path: Path) -> None:
    """The tree the assembly produces must have no broken link."""
    if not (ROOT / "deploy").is_dir():
        pytest.skip("deploy/ not built; run: mise run site:build")
    problems = check_deploy(ROOT / "deploy", check_external=True)
    assert problems == [], problems


def test_no_placeholder_survives_in_the_source() -> None:
    """`{{BASE}}` and friends must be substituted at build time."""
    for path in (COMPAT_PAGE, ROOT / "compat" / "site" / "compat.js", ROOT / "compat" / "site" / "compat.css"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\{\{PYSC_[A-Z_]+\}\}", text), path


def test_compat_page_is_plain_html() -> None:
    """No framework: the page ships plain HTML, CSS and JavaScript."""
    assert (ROOT / "compat" / "site" / "compat.js").is_file()
    html = COMPAT_PAGE.read_text(encoding="utf-8")
    assert 'type="module"' in html or "<script" in html
    for marker in ("react", "vue.js", "svelte", "jquery"):
        assert marker not in html.lower(), marker


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
    assert "git push" not in workflow.replace("git push --force origin", "")


def test_docs_link_to_the_compat_placeholder() -> None:
    """The docs must link through the placeholder, never a hard-coded host."""
    page = (ROOT / "docs" / "guide" / "support-model.md").read_text(encoding="utf-8")
    assert "{{PYSC_COMPAT_URL}}" in page
    assert "pyscriptc-compat.pages.dev" not in page


def test_placeholder_default_is_the_single_site() -> None:
    plugin = (ROOT / "docs" / ".docmd" / "plugins" / "placeholders.js").read_text(encoding="utf-8")
    assert "https://pyscriptc.pages.dev/compat" in plugin
    assert "pyscriptc-compat.pages.dev" not in plugin


def test_compat_data_lives_under_compat_data() -> None:
    assert DATA.is_file()
    assert not (ROOT / "compat-data").exists()


def test_assemble_builds_the_tree(tmp_path: Path, built: Path) -> None:
    """The assembly lays the three parts out and validates them."""
    from scripts.build_site import assemble
    from scripts.build_site import validate as validate_deploy

    docs = tmp_path / "docs"
    (docs / "guide").mkdir(parents=True)
    (docs / "index.html").write_text("<html><body>docs</body></html>", encoding="utf-8")
    (docs / "guide" / "support-model").mkdir()
    (docs / "guide" / "support-model" / "index.html").write_text("<html></html>", encoding="utf-8")

    deploy = assemble(
        {"landing": ROOT / "site" / "landing" / "dist" / "missing", "docs": docs, "compat": built}, tmp_path / "deploy"
    )
    assert (deploy / "index.html").is_file()
    assert (deploy / "docs" / "index.html").is_file()
    assert (deploy / "compat" / "index.html").is_file()
    assert (deploy / "compat" / "data" / "compat.json").is_file()
    assert (deploy / "_headers").is_file()
    assert (deploy / "_redirects").is_file()
    assert validate_deploy(deploy) == 0


def test_assemble_falls_back_without_the_landing_build(tmp_path: Path, built: Path) -> None:
    """A checkout without the Astro output still assembles a valid tree."""
    from scripts.build_site import assemble, fallback_landing

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.html").write_text("<html><body>docs</body></html>", encoding="utf-8")

    deploy = assemble(
        {"landing": ROOT / "site" / "landing" / "dist" / "missing", "docs": docs, "compat": built}, tmp_path / "deploy"
    )
    assert (deploy / "index.html").is_file()
    assert (deploy / "logo-256.png").is_file()
    assert fallback_landing().is_dir()


# The page leads with what does not work.


def test_page_carries_the_failures_first_sections() -> None:
    """The divergences section comes before every support table."""
    html = COMPAT_PAGE.read_text(encoding="utf-8")
    divergences_at = html.index('id="divergences"')
    for section in ("language", "builtins", "stdlib", "pypi", "fences"):
        assert html.index(f'id="{section}"') > divergences_at, section


def test_page_has_a_target_section() -> None:
    html = COMPAT_PAGE.read_text(encoding="utf-8")
    assert 'id="targets"' in html
    assert "native" in html
    assert "experimental" in html


def test_page_has_a_stale_banner() -> None:
    html = COMPAT_PAGE.read_text(encoding="utf-8")
    assert 'id="stale"' in html
    assert "stale" in html.lower()


def test_page_links_the_summary_data() -> None:
    html = COMPAT_PAGE.read_text(encoding="utf-8")
    assert "data/summary.json" in html


def test_page_has_deep_link_anchors() -> None:
    """The JS gives every fence code, module, package, divergence and target
    a stable id; the page source cannot test the JS, but it can pin the
    sections the anchors land in."""
    js = (ROOT / "compat" / "site" / "compat.js").read_text(encoding="utf-8")
    for prefix in ("fence-", "package-", "stdlib-", "divergence-", "target-"):
        assert f'"{prefix}"' in js, prefix


def test_page_needs_no_network_outside_the_origin() -> None:
    """No CDN, no webfont, no third-party runtime call."""
    html = COMPAT_PAGE.read_text(encoding="utf-8")
    for forbidden in ("cdn.", "googleapis", "unpkg", "jsdelivr", "fonts."):
        assert forbidden not in html, forbidden
    js = (ROOT / "compat" / "site" / "compat.js").read_text(encoding="utf-8")
    assert js.count("fetch(") == 1
    assert "XMLHttpRequest" not in js
    assert "importScripts" not in js
    # The one fetch must be the same-origin data file, built from BASE.
    assert 'BASE + "data/compat.json"' in js


def test_page_uses_the_landing_design_tokens() -> None:
    """The tokens are the landing page's: the obsidian canvas, the cyan
    accent, the system font stacks."""
    css = (ROOT / "compat" / "site" / "compat.css").read_text(encoding="utf-8")
    assert "--bg: #06080d" in css  # the landing --color-bg-base
    assert "#00c2ff" in css  # the landing flagship cyan
    assert "-apple-system" in css  # the landing --font-sans
    assert "@font-face" not in css  # no webfont


def test_page_filters_and_counts_are_labelled() -> None:
    """Every search input carries an accessible label."""
    html = COMPAT_PAGE.read_text(encoding="utf-8")
    assert html.count('aria-label="Filter') >= 7


def test_the_summary_travels_with_the_build(built: Path) -> None:
    if not DATA_SUMMARY.exists():
        pytest.skip("the summary file is not built yet")
    assert (built / "data" / "summary.json").is_file()
    summary = json.loads((built / "data" / "summary.json").read_text(encoding="utf-8"))
    assert validate_summary(summary) == []


def test_build_still_works_without_the_summary(tmp_path: Path, built: Path) -> None:
    """The summary is optional for the compat page itself."""
    out = tmp_path / "no-summary"
    build_compat(out, DATA, "/compat/", tmp_path / "absent-summary.json")
    assert (out / "data" / "compat.json").is_file()
    assert not (out / "data" / "summary.json").exists()


def test_nightly_carries_the_summary_in_the_bot_pr() -> None:
    workflow = (ROOT / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")
    assert "--summary compat/data/summary.json" in workflow
    assert "git add compat/data/compat.json compat/data/summary.json" in workflow


def test_site_workflow_rebuilds_on_a_summary_change() -> None:
    workflow = (ROOT / ".github" / "workflows" / "site.yml").read_text(encoding="utf-8")
    assert workflow.count("compat/data/summary.json") == 2
