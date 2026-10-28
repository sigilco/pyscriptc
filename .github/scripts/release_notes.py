"""Print the CHANGELOG.md section for a release tag, then the static release footer.

usage: release_notes.py TAG CHANGELOG FOOTER [REPO]

Fails if CHANGELOG has no `## [X.Y.Z]` section for TAG, so a release cannot
publish notes that disagree with (or lack) the changelog.

Beta and rc tags (v0.6.0-beta.1, PEP 440 0.6.0b1) additionally get a "How to
test" block: what a beta is, the install lines per channel and where to report.

The release body is: `## vTAG (date)`, the changelog section verbatim, then the
optional contributor blocks (only when external contributors exist), the
compare link to the previous tag and the static footer. Contributor credits live
only in the release body, never in CHANGELOG.md, so the changelog stays hand
editable.

Contributor detection needs `gh` and `GITHUB_REPOSITORY`; when either is absent
(local runs, tests) the blocks are skipped with a notice on stderr. Agents and
maintainers are filtered through `.github/maintainers.txt`: logins ending in
`[bot]` are dropped, as are the `cursor` and `devin-ai-integration` agents (their
commits act for the maintainer).

REPO comes from argv or `GITHUB_REPOSITORY` (the workflow passes both); the
compare link falls back to `sigilco/pyscriptc` when neither is set.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

PRE_TAG = re.compile(r"^v(\d+\.\d+\.\d+)-(beta|rc)\.(\d+)$")

HOW_TO_TEST = """\
## How to test

This is a beta: an early, opt-in build of the upcoming {base} release. It is not the
stable release. pip, uv and PyPI treat it as a pre-release, so default installs never
see it.

Install it in a scratch virtualenv or tool slot (it replaces any stable pyscriptc in
that environment):

```sh
# uv tool (isolated)
uv tool install --prerelease allow pyscriptc=={version}

# pip (in a venv)
pip install --pre 'pyscriptc=={version}'
```

Prebuilt binaries are attached below; verify them against `sha256sums.txt`. Roll back
to stable with `uv tool install pyscriptc` (or `pip install pyscriptc`): the stable
version always wins its channel back. Full details, scope and rollback notes:
https://pyscriptc.pages.dev/docs/getting-started/beta/.

Please report anything you hit with the beta-feedback template:
https://github.com/{repo}/issues/new?template=beta-feedback.md
"""

AGENT_LOGINS = {"cursor", "cursoragent", "cursor-agent", "devin-ai-integration"}
MAINTAINERS_FILE = Path(".github/maintainers.txt")


def section(changelog: str, version: str) -> str | None:
    lines = changelog.splitlines()
    head = re.compile(rf"^## \[{re.escape(version)}\](\s|$)")
    start = next((i for i, line in enumerate(lines) if head.match(line)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## [")), len(lines))
    return "\n".join(lines[start + 1 : end]).strip()


def heading_date(changelog: str, version: str) -> str | None:
    head = re.compile(rf"^## \[{re.escape(version)}\]\s+-\s+(\d{{4}}-\d{{2}}-\d{{2}})\s*$")
    return next((m.group(1) for line in changelog.splitlines() if (m := head.match(line))), None)


def previous_tag(changelog: str, version: str) -> str | None:
    """The version of the next `## [` heading below this one, that is the release before it."""
    lines = changelog.splitlines()
    head = re.compile(r"^## \[([^\]]+)\](\s|$)")
    start = next((i for i, line in enumerate(lines) if line.startswith(f"## [{version}]")), None)
    if start is None:
        return None
    below = (m.group(1) for i in range(start + 1, len(lines)) if (m := head.match(lines[i])))
    return next((v for v in below if v != "Unreleased"), None)


def compare_link(repo: str | None, prev: str | None, tag: str) -> str:
    base = f"https://github.com/{repo}/compare" if repo else "https://github.com/sigilco/pyscriptc/compare"
    return f"{base}/v{prev}...v{tag}" if prev else f"{base}/v{tag}"


def load_maintainers(path: Path | str = MAINTAINERS_FILE) -> set[str]:
    p = Path(path)
    if not p.exists():
        return set()
    return {
        line.strip().lstrip("@") for line in p.read_text().splitlines() if line.strip() and not line.startswith("#")
    }


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def gh_available() -> bool:
    try:
        subprocess.run(("gh", "--version"), check=True, capture_output=True)
    except OSError, subprocess.CalledProcessError:
        return False
    return True


def has_breaking_commits(prev: str | None) -> bool:
    """True when a `!` or `BREAKING CHANGE:` commit exists between PREV and HEAD.

    HEAD is the tag commit when the notes are built from a tag checkout.
    """
    rng = f"v{prev}..HEAD" if prev else "HEAD"
    try:
        out = _run(["git", "log", "--no-merges", "--format=%s%n%b", rng])
    except OSError, subprocess.CalledProcessError:
        return False
    for line in out.splitlines():
        if re.match(r"^\w+(\([^)]*\))?!:", line) or line.startswith("BREAKING CHANGE:"):
            return True
    return False


def contributors(
    repo: str | None,
    prev: str | None,
    tag: str,
    maintainers: set[str],
) -> tuple[list[str], list[str]]:
    """(contributors, new_contributors) for the PREV...TAG range.

    Drops bot and agent logins and anyone in MAINTAINERS. New contributors are
    those with no commit reachable from PREV. Empty when there is nothing to
    credit, which is the case for every release so far.
    """
    if not repo or not gh_available():
        print("note: gh unavailable, skipping contributor credits", file=sys.stderr)
        return [], []
    rng = f"v{prev}...v{tag}" if prev else tag
    try:
        raw = _run(
            [
                "gh",
                "api",
                f"repos/{repo}/compare/{rng}",
                "--paginate",
                "--jq",
                '.commits[] | [.author.login // "", .commit.author.email] | @tsv',
            ]
        )
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
        print(f"note: gh api compare failed ({detail[0]}), skipping contributor credits", file=sys.stderr)
        return [], []

    emails: dict[str, str] = {}
    for line in raw.splitlines():
        login, _, email = line.partition("\t")
        login = login.strip().lstrip("@")
        if not login or login.endswith("[bot]") or login.lower() in AGENT_LOGINS or login in maintainers:
            continue
        emails.setdefault(login, email.strip())
    if not emails:
        return [], []
    people = sorted(emails)
    new = [login for login in people if not _has_commit_before(prev, emails[login])]
    return people, new


def _has_commit_before(prev: str | None, email: str) -> bool:
    """True when the author already had a commit reachable from PREV."""
    if not prev or not email:
        return False
    try:
        out = _run(["git", "log", f"v{prev}", f"--author={email}", "--format=%H", "-1"])
    except OSError, subprocess.CalledProcessError:
        return True
    return bool(out.strip())


def credits_block(repo: str | None, prev: str | None, tag: str, maintainers: set[str]) -> str:
    people, new = contributors(repo, prev, tag, maintainers)
    if not people:
        return ""
    lines = ["### Contributors", "", "Thanks to " + ", ".join(f"@{p}" for p in people) + "."]
    if new:
        lines += ["", "### New contributors", ""]
        lines += [f"- @{p} made their first contribution" for p in new]
    return "\n".join(lines) + "\n"


def build_body(
    tag: str,
    changelog: str,
    footer: str,
    *,
    repo: str | None = None,
    maintainers: set[str] | None = None,
    credits: str = "",
) -> str:
    m = PRE_TAG.match(tag)
    version = m.group(1) + {"beta": "b", "rc": "rc"}[m.group(2)] + m.group(3) if m else tag.removeprefix("v")
    body = section(changelog, version)
    if not body:
        raise ValueError(f"CHANGELOG.md has no non-empty section for [{version}]")
    if has_breaking_commits(previous_tag(changelog, version)) and "### Breaking changes" not in body:
        raise ValueError(f"section [{version}] has breaking commits but no '### Breaking changes' entry")
    if credits:
        credits = credits.rstrip()
    if maintainers is not None and not credits:
        credits = credits_block(repo, previous_tag(changelog, version), tag, maintainers).rstrip()
    date = heading_date(changelog, version)
    out = [f"## {tag}" + (f" ({date})" if date else ""), "", body, ""]
    if credits:
        out += [credits, ""]
    out += [f"**Full changelog**: {compare_link(repo, previous_tag(changelog, version), version)}", ""]
    if m:
        out += [HOW_TO_TEST.format(base=m.group(1), version=version, repo=repo or "sigilco/pyscriptc"), ""]
    out += [footer.rstrip(), ""]
    return "\n".join(out)


def main() -> int:
    argv = sys.argv[1:]
    if len(argv) not in (3, 4):
        print(__doc__, file=sys.stderr)
        return 2
    tag, changelog, footer = argv[:3]
    repo = argv[3] if len(argv) == 4 else os.environ.get("GITHUB_REPOSITORY")
    changelog_text = Path(changelog).read_text(encoding="utf-8")
    footer_text = Path(footer).read_text(encoding="utf-8")
    try:
        print(build_body(tag, changelog_text, footer_text, repo=repo, maintainers=load_maintainers()))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
