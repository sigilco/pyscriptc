#!/usr/bin/env python3
"""Release bar for cut-release.yml and the release.yml publish gate.

The decision logic is pure (commit and run lists in, checks out) so it is unit tested
without network. The CLI wraps it with git and gh calls.

Versions follow PEP 440: pyproject.toml says 0.6.0b1 or 0.6.0rc1, the public tag and
release title use the SemVer-style spelling v0.6.0-beta.1 / v0.6.0-rc.2 (what
PyPI normalizes them to). `tag_version` and `release_tag` map between the two.

Subcommands:
  plan              compute the next version, the changelog entry and the bar report
  check-rehearsal   fail unless a rehearsal run covers the parent of a tagged release commit
  tag               map a release tag to its PEP 440 version (--check verifies the spelling)

Thresholds come from the environment (set in the top-level env block of cut-release.yml):
PATCH_MIN_COMMITS, PATCH_MIN_HOURS, PATCH_MIN_PRS, PATCH_MIN_USER_CHANGES, MINOR_MIN_FEATURES,
MINOR_MIN_DAYS, MAX_PRE.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

USER_CODE_PREFIXES = ("src/", "packaging/", "docs/")
USER_DOCS = frozenset({"README.md"})
RELEASE_COMMIT_FILES = frozenset({"pyproject.toml", "uv.lock", "CHANGELOG.md"})
USER_KINDS = frozenset({"feat", "fix", "perf"})
CONVENTIONAL = re.compile(r"^(\w+)(\([^)]*\))?(!)?: (.+)$")
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:(b|rc)(\d+))?$")
STABLE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# Public spelling of a prerelease tag: v0.6.0-beta.1, v0.6.0-rc.2 (the 0.6.0b1 / 0.6.0rc2 forms).
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-(beta|rc)\.(\d+))?$")
KIND_PUBLIC = {"b": "beta", "rc": "rc"}
KIND_PEP440 = {"beta": "b", "rc": "rc"}


@dataclass(frozen=True)
class Thresholds:
    patch_min_commits: int = 10
    patch_min_hours: float = 12
    patch_min_prs: int = 3
    patch_min_user_changes: int = 1
    minor_min_features: int = 2
    minor_min_days: float = 5
    max_pre: int = 2

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Thresholds:
        e = os.environ if env is None else env
        d = cls()
        return cls(
            patch_min_commits=int(e.get("PATCH_MIN_COMMITS", d.patch_min_commits)),
            patch_min_hours=float(e.get("PATCH_MIN_HOURS", d.patch_min_hours)),
            patch_min_prs=int(e.get("PATCH_MIN_PRS", d.patch_min_prs)),
            patch_min_user_changes=int(e.get("PATCH_MIN_USER_CHANGES", d.patch_min_user_changes)),
            minor_min_features=int(e.get("MINOR_MIN_FEATURES", d.minor_min_features)),
            minor_min_days=float(e.get("MINOR_MIN_DAYS", d.minor_min_days)),
            max_pre=int(e.get("MAX_PRE", e.get("MAX_RC", d.max_pre))),
        )


@dataclass(frozen=True)
class Commit:
    subject: str
    files: tuple[str, ...] = ()


@dataclass
class Stats:
    commits: list[Commit] = field(default_factory=list)
    merged_prs: int = 0
    hours_since_tag: float | None = None
    days_since_minor: float | None = None
    milestone_found: bool = False
    milestone_open_issues: int = 0
    rehearsed: bool = False


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # pass | fail | manual
    detail: str


def is_user_path(path: str) -> bool:
    return path.startswith(USER_CODE_PREFIXES) or path in USER_DOCS


def commit_kind(subject: str) -> str | None:
    m = CONVENTIONAL.match(subject)
    return m.group(1) if m else None


def user_visible(commits: list[Commit], kinds: frozenset[str] = USER_KINDS) -> list[Commit]:
    """Commits of the given conventional kinds that touch a user path."""
    return [c for c in commits if commit_kind(c.subject) in kinds and any(is_user_path(f) for f in c.files)]


def parse_version(text: str) -> tuple[int, int, int, str | None, int | None]:
    """Parse X.Y.Z, X.Y.ZrcN or X.Y.ZbN into (major, minor, patch, kind, num)."""
    m = VERSION.match(text)
    if not m:
        raise ValueError(f"unsupported version {text!r}; expected X.Y.Z, X.Y.ZbN or X.Y.ZrcN")
    kind, num = m.group(4), m.group(5)
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), kind, None if num is None else int(num)


def tag_version(version: str) -> str:
    """Public tag/public spelling for a version: 0.6.0b1 -> v0.6.0-beta.1."""
    major, minor, patch, kind, num = parse_version(version)
    pre = "" if kind is None else f"-{KIND_PUBLIC[kind]}.{num}"
    return f"v{major}.{minor}.{patch}{pre}"


def release_tag(tag: str) -> str:
    """Inverse of tag_version: v0.6.0-beta.1 -> 0.6.0b1 (the PEP 440 spelling)."""
    m = TAG_RE.match(tag)
    if not m:
        raise ValueError(f"unsupported tag {tag!r}; expected vX.Y.Z, vX.Y.Z-beta.N or vX.Y.Z-rc.N")
    major, minor, patch, kind, num = m.groups()
    pre = "" if kind is None else KIND_PEP440[kind] + num
    return f"{major}.{minor}.{patch}{pre}"


def _bumped(major: int, minor: int, patch: int, part: str) -> tuple[int, int, int]:
    if part == "major":
        return major + 1, 0, 0
    if part == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def next_version(current: str, bump: str, pre_of: str = "patch", kind: str = "rc") -> str:
    """Next version. A prerelease counts up in its kind; a stable bump promotes it to its base."""
    major, minor, patch, cur_kind, num = parse_version(current)
    if bump == "prerelease":
        if cur_kind is not None:
            return f"{major}.{minor}.{patch}{cur_kind}{num + 1}"
        major, minor, patch = _bumped(major, minor, patch, pre_of)
        pre = "b" if kind == "beta" else "rc"
        return f"{major}.{minor}.{patch}{pre}1"
    if cur_kind is not None:
        return f"{major}.{minor}.{patch}{cur_kind}{num}"
    major, minor, patch = _bumped(major, minor, patch, bump)
    return f"{major}.{minor}.{patch}"


def release_class(version: str, hotfix: bool = False) -> str:
    major, minor, patch, kind, _num = parse_version(version)
    if kind is not None:
        return "prerelease"
    if hotfix:
        return "hotfix"
    if patch > 0:
        return "patch"
    return "minor" if minor > 0 else "major"


def milestone_for(milestones: list[dict], version: str) -> dict | None:
    """Milestone whose title is vX.Y.Z or starts with 'vX.Y.Z ' (e.g. 'v0.4.1 alpha')."""
    want = f"v{version}"
    for m in milestones:
        title = str(m.get("title", ""))
        if title == want or title.startswith(want + " "):
            return m
    return None


def rehearsal_title(sha: str) -> str:
    return f"rehearsal {sha}"


def has_rehearsal(runs: list[dict], sha: str) -> bool:
    """A successful dispatch run whose run-name says it rehearsed exactly this commit.

    release.yml names dispatch runs 'rehearsal <ref>' and ref defaults to the dispatched
    commit, so the title (not headSha) is what identifies the rehearsed commit.
    """
    return any(
        r.get("conclusion") == "success" and r.get("displayTitle") == rehearsal_title(sha)
        for r in runs
        if r.get("event", "workflow_dispatch") == "workflow_dispatch"
    )


def rehearsal_covers(
    runs: list[dict], tagged_sha: str, parent_sha: str | None, changed_files: list[str]
) -> tuple[bool, str]:
    """Publish gate: the tagged commit or its parent was rehearsed, and the diff is release-only."""
    if has_rehearsal(runs, tagged_sha):
        return True, f"tagged commit {tagged_sha[:12]} was rehearsed"
    if parent_sha is None:
        return False, f"no rehearsal of {tagged_sha[:12]} and it has no parent"
    if not has_rehearsal(runs, parent_sha):
        return False, f"no successful rehearsal run for {tagged_sha[:12]} or its parent {parent_sha[:12]}"
    extra = sorted(set(changed_files) - RELEASE_COMMIT_FILES)
    if extra:
        return False, f"parent {parent_sha[:12]} was rehearsed but the release commit also changes: {', '.join(extra)}"
    return (
        True,
        f"parent {parent_sha[:12]} was rehearsed; release commit touches only pyproject.toml, uv.lock, CHANGELOG.md",
    )


def _chk(name: str, ok: bool, detail: str) -> Check:
    return Check(name, "pass" if ok else "fail", detail)


def evaluate(cls: str, version: str, stats: Stats, th: Thresholds, *, bump: str = "patch") -> list[Check]:
    """Bar checks for a release class. Status is pass, fail or manual (not automatable)."""
    out: list[Check] = []
    n_commits = len(stats.commits)
    n_user = len(user_visible(stats.commits))
    n_feat = len(user_visible(stats.commits, frozenset({"feat"})))

    if cls == "patch":
        ok = n_user >= th.patch_min_user_changes or stats.merged_prs >= th.patch_min_prs
        out.append(
            _chk(
                "content",
                ok,
                f"{n_user} user-visible feat/fix/perf (need {th.patch_min_user_changes}) or "
                f"{stats.merged_prs} merged PRs (need {th.patch_min_prs})",
            )
        )
        out.append(
            _chk(
                "commits",
                n_commits >= th.patch_min_commits,
                f"{n_commits} since last tag (need {th.patch_min_commits})",
            )
        )
        hrs = stats.hours_since_tag
        out.append(
            _chk(
                "gap",
                hrs is None or hrs >= th.patch_min_hours,
                "no previous tag" if hrs is None else f"{hrs:.1f} h since last tag (need {th.patch_min_hours:g})",
            )
        )
    elif cls == "hotfix":
        out.append(
            _chk("hotfix bump", bump == "patch", "hotfix must use bump=patch" if bump != "patch" else "patch bump")
        )
        out.append(
            Check(
                "hotfix evidence",
                "manual",
                "linked issue labelled hotfix reproduces an install or first-run break (content, gap skipped)",
            )
        )
    elif cls in ("minor", "major"):
        out.append(
            _chk(
                "features",
                n_feat >= th.minor_min_features,
                f"{n_feat} user-visible feat commits (need {th.minor_min_features})",
            )
        )
        d = stats.days_since_minor
        out.append(
            _chk(
                "minor gap",
                d is None or d >= th.minor_min_days,
                "no previous minor" if d is None else f"{d:.1f} d since previous minor (need {th.minor_min_days:g})",
            )
        )
        if not stats.milestone_found:
            out.append(Check("milestone", "fail", f"no milestone titled v{version} (or 'v{version} ...')"))
        else:
            out.append(
                _chk(
                    "milestone", stats.milestone_open_issues == 0, f"{stats.milestone_open_issues} open issues (need 0)"
                )
            )
        out.append(
            Check(
                "receipt",
                "manual",
                "measured receipt in the changelog (census delta, boxed ledger window or perf number with baseline)",
            )
        )
    elif cls == "prerelease":
        kind, num = parse_version(version)[3:]
        n = num or 0
        name = "rc count" if kind == "rc" else "beta count"
        out.append(
            _chk(
                name,
                n <= th.max_pre,
                f"{kind}{n} (at most {th.max_pre} iterations, then fix the cause)",
            )
        )
        if kind == "b":
            out.append(
                _chk(
                    "features",
                    n_feat >= th.minor_min_features,
                    f"{n_feat} user-visible feat commits since the last stable (need {th.minor_min_features})",
                )
            )
            out.append(
                Check(
                    "triage",
                    "manual",
                    "every open milestone issue is in the beta or moved to Future (issue link in the notes)",
                )
            )
    else:
        raise ValueError(f"unknown release class {cls!r}")

    out.append(
        _chk(
            "rehearsal",
            stats.rehearsed,
            "successful rehearsal run on the base commit"
            if stats.rehearsed
            else "no successful rehearsal run on the base commit (dispatch release.yml with the full SHA)",
        )
    )
    return out


def passed(checks: list[Check]) -> bool:
    return all(c.status != "fail" for c in checks)


def render_report(checks: list[Check], version: str, cls: str, bypass_reason: str = "") -> str:
    icon = {"pass": "pass", "fail": "FAIL", "manual": "manual"}
    lines = [f"### Release bar for v{version} ({cls})", "", "| check | result | detail |", "|---|---|---|"]
    lines += [f"| {c.name} | {icon[c.status]} | {c.detail} |" for c in checks]
    lines.append("")
    failing = [c.name for c in checks if c.status == "fail"]
    if not failing:
        lines.append("Bar passed.")
    elif bypass_reason.strip():
        lines.append(f"Bar failed on: {', '.join(failing)}. BYPASSED with reason: {bypass_reason.strip()}")
    else:
        lines.append(f"Bar failed on: {', '.join(failing)}. Fix them or re-run with bypass_reason.")
    return "\n".join(lines) + "\n"


def changelog_entry(subjects: list[str], version: str, today: str) -> str | None:
    """Changelog section from conventional commit subjects, or None when nothing is releasable."""
    sections: dict[str, list[str]] = {
        k: [] for k in ("breaking", "feat", "fix", "perf", "refactor", "revert", "docs", "build", "ci", "other")
    }
    labels = {
        "breaking": "Breaking", "feat": "Added", "fix": "Fixed", "perf": "Performance", "refactor": "Changed",
        "revert": "Reverted", "docs": "Docs", "build": "Build", "ci": "CI", "other": "Other",
    }  # fmt: skip
    skip = {"chore", "test", "style"}
    for subject in subjects:
        if subject.startswith("chore(release)"):
            continue
        m = CONVENTIONAL.match(subject)
        if not m:
            sections["other"].append(subject)
            continue
        kind, bang, desc = m.group(1), m.group(3), m.group(4)
        if bang:
            sections["breaking"].append(desc)
        elif kind in skip:
            continue
        elif kind in sections:
            sections[kind].append(desc)
        else:
            sections["other"].append(subject)
    if not any(sections.values()):
        return None
    lines = [f"## [{version}] - {today}", ""]
    for key, items in sections.items():
        if items:
            lines.append(f"### {labels[key]}")
            lines.extend(f"- {i}" for i in items)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------- git and gh wrappers


def _run(*cmd: str) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def _parse_log(raw: str) -> list[Commit]:
    commits = []
    for rec in raw.split("\x1e"):
        if not rec.strip():
            continue
        head, _, rest = rec.partition("\n")
        _sha, _, subject = head.partition("\x1f")
        commits.append(Commit(subject, tuple(f for f in rest.splitlines() if f)))
    return commits


def _tag_date(tag: str) -> dt.datetime:
    return dt.datetime.fromisoformat(
        _run("git", "for-each-ref", "--format=%(creatordate:iso-strict)", f"refs/tags/{tag}").strip()
    )


def stable_tags() -> list[str]:
    tags = _run("git", "tag", "--list", "v*").split()
    return sorted(
        (t for t in tags if STABLE_TAG.match(t)),
        key=lambda t: tuple(int(x) for x in STABLE_TAG.match(t).groups()),
        reverse=True,
    )  # type: ignore[union-attr]


def gh_runs(repo: str) -> list[dict]:
    out = _run("gh", "run", "list", "--repo", repo, "--workflow", "release.yml", "--event", "workflow_dispatch",
               "--limit", "200", "--json", "headSha,conclusion,displayTitle,event")  # fmt: skip
    return json.loads(out)


def tag_for_version(version: str) -> str:
    """Annotated-tag helper kept trivial so callers do not re-derive the public spelling."""
    return tag_version(version)


def cmd_plan(a: argparse.Namespace) -> int:
    th = Thresholds.from_env()
    import tomllib

    text = Path("pyproject.toml").read_text()
    cur = tomllib.loads(text)["project"]["version"]
    new = next_version(cur, a.bump, a.pre_of, a.pre_kind)
    cls = release_class(new, a.hotfix)
    tag = tag_version(new)
    tags = stable_tags()
    last = tags[0] if tags else ""
    rng = f"{last}..HEAD" if last else "HEAD"
    commits = [
        c
        for c in _parse_log(_run("git", "log", "--no-merges", "--name-only", "--format=%x1e%H%x1f%s", rng))
        if not c.subject.startswith("chore(release)")
    ]
    now = dt.datetime.now(dt.UTC)
    stats = Stats(commits=commits)
    if last:
        since = _tag_date(last)
        stats.hours_since_tag = (now - since).total_seconds() / 3600
        iso = since.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        stats.merged_prs = int(
            _run(
                *("gh", "pr", "list", "--repo", a.repo, "--state", "merged", "--base", "master"),
                *("--search", f"merged:>={iso}", "--limit", "1000", "--json", "number", "--jq", "length"),
            ).strip()
            or 0
        )
    minors = [t for t in tags if STABLE_TAG.match(t).group(3) == "0"]  # type: ignore[union-attr]
    if minors:
        stats.days_since_minor = (now - _tag_date(minors[0])).total_seconds() / 86400
    if cls in ("minor", "major"):
        ms = json.loads(_run("gh", "api", f"repos/{a.repo}/milestones?state=all&per_page=100", "--paginate", "--slurp"))
        flat = [m for page in ms for m in page]
        found = milestone_for(flat, new)
        stats.milestone_found = found is not None
        stats.milestone_open_issues = int(found["open_issues"]) if found else 0
    head = _run("git", "rev-parse", "HEAD").strip()
    stats.rehearsed = has_rehearsal(gh_runs(a.repo), head)

    checks = evaluate(cls, new, stats, th, bump=a.bump)
    ok = passed(checks)
    report = render_report(checks, new, cls, a.bypass_reason)
    print(f"version {cur} -> {new} ({cls}); tag {tag}; base commit {head}")
    print(report)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as f:
            f.write(report + "\n")

    entry = changelog_entry([c.subject for c in commits], new, dt.date.today().isoformat())
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if entry is None:
        print(f"nothing to release since {last or 'the first commit'}", file=sys.stderr)
    else:
        (out / "entry.md").write_text(entry)
        print(entry)
    (out / "pyproject.toml").write_text(re.sub(r'^version = "[^"]+"', f'version = "{new}"', text, count=1, flags=re.M))
    (out / "version").write_text(new)
    (out / "tag").write_text(tag + "\n")
    if gho := os.environ.get("GITHUB_OUTPUT"):
        with open(gho, "a") as f:
            f.write(f"version={new}\nclass={cls}\ntag={tag}\nbar_passed={'true' if ok else 'false'}\n")
            f.write(f"releasable={'true' if entry else 'false'}\nbase_sha={head}\n")
    return 0


def cmd_check_rehearsal(a: argparse.Namespace) -> int:
    commit = json.loads(_run("gh", "api", f"repos/{a.repo}/commits/{a.sha}"))
    parents = commit.get("parents") or []
    parent = parents[0]["sha"] if parents else None
    files: list[str] = []
    if parent:
        cmp = json.loads(_run("gh", "api", f"repos/{a.repo}/compare/{parent}...{a.sha}"))
        files = [f["filename"] for f in cmp.get("files", [])]
    ok, msg = rehearsal_covers(gh_runs(a.repo), a.sha, parent, files)
    print(("OK: " if ok else "FAIL: ") + msg)
    if not ok:
        print(
            "Dispatch a rehearsal first (gh workflow run release.yml -f ref=<full parent SHA>), then re-run this job.",
            file=sys.stderr,
        )
    return 0 if ok else 1


def cmd_tag(a: argparse.Namespace) -> int:
    """Map between a tag and its PEP 440 version. With --check, both must agree."""
    try:
        version = release_tag(a.tag)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    print(version)
    if a.check:
        expected = tag_version(version)
        if expected != a.tag:
            print(
                f"tag and version disagree: {a.tag} maps to {version}, but {version} maps back to {expected}",
                file=sys.stderr,
            )
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("plan")
    pl.add_argument("--bump", required=True, choices=["patch", "minor", "major", "prerelease"])
    pl.add_argument("--pre-of", default="patch", choices=["patch", "minor", "major"])
    pl.add_argument("--pre-kind", default="rc", choices=["beta", "rc"])
    pl.add_argument("--hotfix", action="store_true")
    pl.add_argument("--bypass-reason", default="")
    pl.add_argument("--repo", required=True)
    pl.add_argument("--out-dir", default="/tmp/cut-release")
    pl.set_defaults(fn=cmd_plan)
    cr = sub.add_parser("check-rehearsal")
    cr.add_argument("--sha", required=True, help="the tagged (release) commit")
    cr.add_argument("--repo", required=True)
    cr.set_defaults(fn=cmd_check_rehearsal)
    tg = sub.add_parser("tag")
    tg.add_argument("tag", help="a release tag: vX.Y.Z, vX.Y.Z-beta.N or vX.Y.Z-rc.N")
    tg.add_argument("--check", action="store_true", help="also verify the tag is the canonical spelling")
    tg.set_defaults(fn=cmd_tag)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
