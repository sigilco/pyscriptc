"""Tests for .github/scripts/release_notes.py: section parsing, compare links and contributor blocks.

No network: contributors() is never called directly; the credits block is passed
to build_body() pre-rendered, and the gh path is tested through injection.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github" / "scripts"))

import release_notes as rn  # noqa: E402

CHANGELOG = """\
# Changelog

## [Unreleased]

Nothing yet.

## [0.4.1] - 2026-10-21

Alpha fixes.

### Fixes

- **First-run works.** Fixed.

## [0.4.0] - 2026-10-20

The big one.

- Refactor: split.

## [0.3.0] - 2026-10-19

Earlier.

- Old.
"""


def test_section_extracts_between_headings():
    body = rn.section(CHANGELOG, "0.4.1")
    assert body.startswith("Alpha fixes.")
    assert "First-run works." in body
    assert "0.4.0" not in body


def test_section_missing_version_is_none():
    assert rn.section(CHANGELOG, "9.9.9") is None
    assert rn.section(CHANGELOG, "0.4") is None


def test_heading_date():
    assert rn.heading_date(CHANGELOG, "0.4.1") == "2026-10-21"
    assert rn.heading_date(CHANGELOG, "0.3.0") == "2026-10-19"
    assert rn.heading_date("## [1.0.0]\n", "1.0.0") is None


def test_previous_tag_skips_unreleased():
    assert rn.previous_tag(CHANGELOG, "0.4.1") == "0.4.0"
    assert rn.previous_tag(CHANGELOG, "0.4.0") == "0.3.0"
    assert rn.previous_tag(CHANGELOG, "0.3.0") is None
    assert rn.previous_tag(CHANGELOG, "9.9.9") is None


def test_compare_link():
    assert (
        rn.compare_link("sigilco/pyscriptc", "0.4.0", "0.4.1")
        == "https://github.com/sigilco/pyscriptc/compare/v0.4.0...v0.4.1"
    )
    assert rn.compare_link("sigilco/pyscriptc", None, "0.1.0") == "https://github.com/sigilco/pyscriptc/compare/v0.1.0"


def test_load_maintainers(tmp_path: Path):
    p = tmp_path / "maintainers.txt"
    p.write_text("# comment\n\nespetro\n@quim\n")
    assert rn.load_maintainers(p) == {"espetro", "quim"}
    assert rn.load_maintainers(tmp_path / "missing") == set()


def test_contributors_filters_bots_and_maintainers(monkeypatch: pytest.MonkeyPatch):
    compare_output = (
        "espetro\ta@b.c\n"
        "alice\talice@users.noreply.github.com\n"
        "renovate[bot]\tx@users.noreply.github.com\n"
        "cursoragent\tc@d.e\n"
        "\tunlinked@example.com\n"
    )

    def fake_run(cmd):
        if cmd[0] == "git":
            return ""  # no commit by alice before the previous tag
        assert cmd[0] == "gh", cmd
        if "compare" in cmd[2]:
            return compare_output
        assert cmd[1] == "api", cmd
        return ""

    monkeypatch.setattr(rn, "_run", fake_run)
    monkeypatch.setattr(rn, "gh_available", lambda: True)
    people, new = rn.contributors("sigilco/pyscriptc", "0.4.0", "0.4.1", {"espetro"})
    assert people == ["alice"]
    assert new == ["alice"]


def test_contributors_empty_without_repo():
    assert rn.contributors(None, "0.4.0", "0.4.1", set()) == ([], [])


def test_contributors_gh_missing(monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setattr(rn, "gh_available", lambda: False)
    assert rn.contributors("sigilco/pyscriptc", "0.4.0", "0.4.1", set()) == ([], [])
    assert "skipping contributor credits" in capsys.readouterr().err


def test_credits_block_both(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(rn, "contributors", lambda *a, **k: (["alice", "bob"], ["bob"]))
    block = rn.credits_block("sigilco/pyscriptc", "0.4.0", "0.4.1", set())
    assert "### Contributors" in block
    assert "Thanks to @alice, @bob." in block
    assert "### New contributors" in block
    assert "- @bob made their first contribution" in block


def test_credits_block_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(rn, "contributors", lambda *a, **k: ([], []))
    assert rn.credits_block("sigilco/pyscriptc", "0.4.0", "0.4.1", set()) == ""


FOOTER = "binary table footer"


def test_build_body_full():
    body = rn.build_body(
        "v0.4.1",
        CHANGELOG,
        FOOTER,
        credits="### Contributors\n\nThanks to @alice.\n",
    )
    assert body.startswith("## v0.4.1 (2026-10-21)\n\nAlpha fixes.")
    assert "### Contributors" in body
    assert "**Full changelog**: https://github.com/sigilco/pyscriptc/compare/v0.4.0...v0.4.1" in body
    assert body.rstrip().endswith(FOOTER)


def test_build_body_fails_on_missing_section():
    with pytest.raises(ValueError, match="no non-empty section"):
        rn.build_body("v9.9.9", CHANGELOG, FOOTER)


def test_build_body_breaking_guard(monkeypatch: pytest.MonkeyPatch):
    changelog = CHANGELOG.replace("## [0.4.1] - 2026-10-21", "## [0.5.0] - 2026-10-22")
    monkeypatch.setattr(rn, "has_breaking_commits", lambda prev: True)
    with pytest.raises(ValueError, match="Breaking changes"):
        rn.build_body("v0.5.0", changelog, FOOTER)
    changelog = changelog.replace("Alpha fixes.", "Alpha fixes.\n\n### Breaking changes\n\n- **Gone.** Migrate.\n")
    body = rn.build_body("v0.5.0", changelog, FOOTER)
    assert "### Breaking changes" in body


def test_has_breaking_commits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import subprocess

    def git(*args: str) -> None:
        subprocess.run(("git", "-C", str(tmp_path), *args), check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "a@b.c")
    git("config", "user.name", "t")
    (tmp_path / "f").write_text("1\n")
    git("add", "-A")
    git("commit", "-qm", "feat: one")
    git("tag", "v0.1.0")
    (tmp_path / "f").write_text("2\n")
    git("add", "-A")
    git("commit", "-qm", "feat!: break")
    monkeypatch.chdir(tmp_path)
    assert rn.has_breaking_commits("0.1.0")
    (tmp_path / "f").write_text("3\n")
    git("add", "-A")
    git("commit", "-qm", "fix: plain")
    assert not rn.has_breaking_commits("0.2.0")
    assert rn.has_breaking_commits("0.1.0")
    assert (
        not rn.has_breaking_commits(None)
        or "plain" in subprocess.run(("git", "log", "--format=%s"), capture_output=True, text=True, cwd=tmp_path).stdout
    )
