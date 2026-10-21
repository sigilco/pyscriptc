"""Unit tests for .github/scripts/release_bar.py (pure functions, no network)."""

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "release_bar", Path(__file__).resolve().parent.parent / ".github" / "scripts" / "release_bar.py"
)
rb = importlib.util.module_from_spec(_SPEC)
sys.modules["release_bar"] = rb
_SPEC.loader.exec_module(rb)

TH = rb.Thresholds()
SRC = ("src/pyscriptc/cli.py",)


def commits(n: int, subject: str = "refactor(x): y", files=("src/a.py",)):
    return [rb.Commit(subject, files) for _ in range(n)]


def failing(checks):
    return {c.name for c in checks if c.status == "fail"}


def test_next_version():
    assert rb.next_version("0.4.0", "patch") == "0.4.1"
    assert rb.next_version("0.4.1", "minor") == "0.5.0"
    assert rb.next_version("0.4.1", "major") == "1.0.0"
    assert rb.next_version("0.4.1", "prerelease") == "0.4.2rc1"
    assert rb.next_version("0.4.1", "prerelease", "minor") == "0.5.0rc1"
    assert rb.next_version("0.5.0rc1", "prerelease") == "0.5.0rc2"
    assert rb.next_version("0.5.0rc2", "minor") == "0.5.0"
    with pytest.raises(ValueError):
        rb.next_version("0.5", "patch")


def test_release_class():
    assert rb.release_class("0.4.1") == "patch"
    assert rb.release_class("0.4.1", hotfix=True) == "hotfix"
    assert rb.release_class("0.5.0") == "minor"
    assert rb.release_class("1.0.0") == "major"
    assert rb.release_class("0.5.0rc1", hotfix=True) == "prerelease"


def test_user_visible_needs_kind_and_path():
    cs = [
        rb.Commit("feat(cli): x", SRC),
        rb.Commit("fix: y", ("tests/test_a.py",)),
        rb.Commit("refactor: z", SRC),
        rb.Commit("perf(rt): w", ("src/pyscriptc/runtime/ps.c",)),
        rb.Commit("fix(docs): d", ("docs/getting-started.md",)),
        rb.Commit("fix: internal doc", ("docs/release-process.md",)),
    ]
    assert len(rb.user_visible(cs)) == 3


def test_milestone_matching():
    ms = [{"title": "v0.4.1 alpha"}, {"title": "v0.5.0"}, {"title": "v0.4.10"}]
    assert rb.milestone_for(ms, "0.4.1")["title"] == "v0.4.1 alpha"
    assert rb.milestone_for(ms, "0.5.0")["title"] == "v0.5.0"
    assert rb.milestone_for(ms, "0.6.0") is None


@pytest.mark.parametrize(
    ("name", "ncommits", "hours", "prs", "kinds"),
    [
        # Real gaps: v0.2.0 -> v0.2.1, v0.2.1 -> v0.3.0, v0.3.0 -> v0.4.0 (no PRs, minutes apart).
        ("v0.2.1", 3, 26 / 60, 0, "test"),
        ("v0.3.0", 11, 60 / 60, 0, "feat"),
        ("v0.4.0", 25, 61 / 60, 0, "refactor"),
    ],
)
def test_real_gaps_fail_patch_bar(name, ncommits, hours, prs, kinds):
    subject = f"{kinds}(x): y"
    stats = rb.Stats(commits=commits(ncommits, subject), merged_prs=prs, hours_since_tag=hours, rehearsed=True)
    checks = rb.evaluate("patch", "0.0.1", stats, TH)
    assert not rb.passed(checks)
    assert "gap" in failing(checks)


def test_v021_also_fails_commits_and_content():
    stats = rb.Stats(commits=commits(3, "test: x"), merged_prs=0, hours_since_tag=0.4, rehearsed=True)
    assert failing(rb.evaluate("patch", "0.2.1", stats, TH)) == {"content", "commits", "gap"}


def test_patch_passes_with_enough_content():
    stats = rb.Stats(commits=commits(12, "fix(cli): x", SRC), merged_prs=0, hours_since_tag=20, rehearsed=True)
    assert rb.passed(rb.evaluate("patch", "0.4.1", stats, TH))


def test_patch_passes_on_prs_alone_but_needs_rehearsal():
    stats = rb.Stats(commits=commits(10), merged_prs=3, hours_since_tag=13)
    assert failing(rb.evaluate("patch", "0.4.1", stats, TH)) == {"rehearsal"}


def test_hotfix_skips_content_and_gap_but_not_rehearsal():
    stats = rb.Stats(commits=commits(1, "fix: x", SRC), hours_since_tag=0.1)
    assert failing(rb.evaluate("hotfix", "0.4.2", stats, TH)) == {"rehearsal"}
    stats.rehearsed = True
    assert rb.passed(rb.evaluate("hotfix", "0.4.2", stats, TH))
    assert "hotfix bump" in failing(rb.evaluate("hotfix", "0.5.0", stats, TH, bump="minor"))


def test_minor_bar():
    feats = commits(2, "feat(x): y", SRC)
    ok = rb.Stats(commits=feats, days_since_minor=6, milestone_found=True, milestone_open_issues=0, rehearsed=True)
    checks = rb.evaluate("minor", "0.5.0", ok, TH)
    assert rb.passed(checks)
    assert any(c.name == "receipt" and c.status == "manual" for c in checks)
    bad = rb.Stats(commits=feats[:1], days_since_minor=2, milestone_found=True, milestone_open_issues=3, rehearsed=True)
    assert failing(rb.evaluate("minor", "0.5.0", bad, TH)) == {"features", "minor gap", "milestone"}
    nomile = rb.Stats(commits=feats, days_since_minor=6, rehearsed=True)
    assert failing(rb.evaluate("minor", "0.5.0", nomile, TH)) == {"milestone"}


def test_prerelease_caps_rc_iterations():
    s = rb.Stats(rehearsed=True)
    assert rb.passed(rb.evaluate("prerelease", "0.5.0rc2", s, TH))
    assert failing(rb.evaluate("prerelease", "0.5.0rc3", s, TH)) == {"rc count"}


def test_thresholds_from_env():
    th = rb.Thresholds.from_env({"PATCH_MIN_COMMITS": "5", "MINOR_MIN_DAYS": "1.5"})
    assert th.patch_min_commits == 5
    assert th.minor_min_days == 1.5
    assert th.patch_min_hours == 12


def run(sha, conclusion="success", title=None):
    return {
        "headSha": "x",
        "conclusion": conclusion,
        "displayTitle": title or f"rehearsal {sha}",
        "event": "workflow_dispatch",
    }


def test_has_rehearsal_matches_title_not_head_sha():
    assert rb.has_rehearsal([run("a" * 40)], "a" * 40)
    assert not rb.has_rehearsal([run("a" * 40, conclusion="failure")], "a" * 40)
    assert not rb.has_rehearsal([run("a" * 40)], "b" * 40)
    # A run dispatched from master for some branch must not vouch for master's own tip.
    assert not rb.has_rehearsal(
        [{"headSha": "m" * 40, "conclusion": "success", "displayTitle": "rehearsal ci/x"}], "m" * 40
    )


def test_rehearsal_covers_publish_gate():
    t, p = "t" * 40, "p" * 40
    files = ["pyproject.toml", "uv.lock", "CHANGELOG.md"]
    assert rb.rehearsal_covers([run(p)], t, p, files)[0]
    assert rb.rehearsal_covers([run(t)], t, p, ["src/x.py"])[0]
    assert not rb.rehearsal_covers([], t, p, files)[0]
    ok, msg = rb.rehearsal_covers([run(p)], t, p, files + ["src/x.py"])
    assert not ok and "src/x.py" in msg
    assert not rb.rehearsal_covers([run(p)], t, None, [])[0]


def test_changelog_entry():
    subjects = ["feat(cli): add x", "chore: skip", "fix!: break", "chore(release): v0.1.0", "plain subject", "test: t"]
    entry = rb.changelog_entry(subjects, "0.2.0", "2026-10-21")
    assert entry.startswith("## [0.2.0] - 2026-10-21\n")
    assert "### Breaking\n- break" in entry
    assert "### Added\n- add x" in entry
    assert "- plain subject" in entry
    assert "skip" not in entry
    assert rb.changelog_entry(["chore: a", "test: b"], "0.2.0", "d") is None


def test_report_marks_bypass():
    checks = [rb.Check("gap", "fail", "0.1 h"), rb.Check("rehearsal", "pass", "ok")]
    assert "BYPASSED with reason: broken install" in rb.render_report(checks, "0.4.1", "patch", "broken install")
    assert "re-run with bypass_reason" in rb.render_report(checks, "0.4.1", "patch")
