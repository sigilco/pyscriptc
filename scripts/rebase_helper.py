"""Rebase the current branch onto origin/master, resolving known mechanical conflicts.

Run from a worktree on the PR branch:

    uv run python scripts/rebase_helper.py [--onto REF] [--no-fetch]

The rebase-onto-master conflicts that parallel MIR/pr work hits fall into
three mechanical buckets (see .agents/docs/parallel-pr-rules.md):

- `data/compat/support.json`: generated, never hand-merged. Resolved by
  taking the onto side and regenerating after the rebase.
- `tests/census_pins/` shards (and the retired `tests/census_pins.json`):
  resolved by taking either side mid-rebase, then `repin_census.py`
  rewrites every shard from the merged tree.
- fixture sample lists: gone (tests discover fixtures), so nothing to do.

Any other conflicted path stops the helper with the file list and leaves
the rebase in progress for manual resolution (`git status`, fix, then
`git rebase --continue`, or `git rebase --abort`). After a clean rebase the
helper regenerates `data/compat/support.json` and the census shards; a
support.json diff that touches only the `generated`/`commit` metadata
fields is dropped, and any other dirty path is printed without committing,
so the regeneration commit stays a deliberate step.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SUPPORT_JSON = "data/compat/support.json"
CENSUS_PINS_PREFIX = "tests/census_pins"
LEGACY_PINS = "tests/census_pins.json"

# Paths the helper may resolve mechanically. Anything else stops the rebase.
MECHANICAL_PREFIXES = (CENSUS_PINS_PREFIX,)
MECHANICAL_FILES = (SUPPORT_JSON, LEGACY_PINS)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, check=check, capture_output=True, text=True)


def _rebase_dir(name: str) -> bool:
    proc = git("rev-parse", "--git-path", name, check=False)
    if proc.returncode != 0:
        return False
    path = Path(proc.stdout.strip())
    if not path.is_absolute():
        path = ROOT / path
    return path.is_dir()


def rebase_in_progress() -> bool:
    return _rebase_dir("rebase-merge") or _rebase_dir("rebase-apply")


def unmerged_paths() -> list[str]:
    out = git("diff", "--name-only", "--diff-filter=U").stdout
    return [ln for ln in out.splitlines() if ln]


def is_mechanical(path: str) -> bool:
    return path in MECHANICAL_FILES or path.startswith(MECHANICAL_PREFIXES)


def resolve_mechanical(paths: list[str]) -> None:
    """Resolve the mechanical conflicts (all entries are known-mechanical)."""
    for path in paths:
        if path == SUPPORT_JSON:
            # Generated file: the onto side is as good a base as ours; the
            # post-rebase regeneration produces the correct bytes either way.
            git("checkout", "--ours", "--", path)
            git("add", "--", path)
        elif path == LEGACY_PINS:
            # The single pins file is retired; the shard dir wins.
            git("rm", "-f", "--", path)
        elif path.startswith(CENSUS_PINS_PREFIX):
            # Take the incoming side mid-rebase; repin_census rewrites every
            # shard from the merged tree once the rebase completes.
            proc = git("checkout", "--theirs", "--", path, check=False)
            if proc.returncode != 0:
                # e.g. deleted on one side: drop it, the final repin restores it.
                git("rm", "-f", "--", path, check=False)
            git("add", "--", path, check=False)


def regenerate() -> int:
    rc = 0
    for cmd in (
        ["python", "scripts/repin_census.py"],
        ["python", "scripts/gen_support_table.py"],
    ):
        proc = subprocess.run(["uv", "run", *cmd], cwd=ROOT)
        rc = rc or proc.returncode
    return rc


# Top-level metadata fields gen_support_table.py stamps on every run; a diff
# that touches only these is not a content change worth a commit.
_METADATA_FIELDS = ('"generated"', '"commit"')


def _content_diff(path: str) -> list[str]:
    diff = git("diff", "-U0", "--", path).stdout
    out = []
    for ln in diff.splitlines():
        if not ln.startswith(("+", "-")) or ln.startswith(("+++", "---")):
            continue
        if any(field in ln for field in _METADATA_FIELDS):
            continue
        out.append(ln)
    return out


def drop_metadata_only_diff(path: str) -> None:
    if path in dirty_paths() and not _content_diff(path):
        git("checkout", "--", path)


def dirty_paths() -> list[str]:
    out = git("status", "--porcelain").stdout
    return [ln[3:].split(" -> ")[-1].strip('"') for ln in out.splitlines() if ln.strip()]


def report_blocked(paths: list[str], mechanical: list[str]) -> int:
    print("rebase-helper: non-mechanical conflicts, stopping with the rebase in progress:")
    for p in paths:
        print(f"  {p}")
    if mechanical:
        print("Left unresolved next to them (resolve or regenerate after continuing):")
        for p in mechanical:
            print(f"  {p}")
    print("Resolve by hand (`git status`), then `git rebase --continue`, or `git rebase --abort`.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onto", default="origin/master", help="rebase target (default: origin/master)")
    parser.add_argument("--no-fetch", action="store_true", help="skip `git fetch origin`")
    args = parser.parse_args(argv)

    if not args.no_fetch:
        if git("fetch", "origin", check=False).returncode != 0:
            print("rebase-helper: git fetch origin failed; continuing with the local refs", file=sys.stderr)

    if rebase_in_progress():
        print("rebase-helper: a rebase is already in progress; resolving its conflicts first")
    else:
        proc = git("rebase", args.onto, check=False)
        if proc.returncode == 0:
            print(f"rebase-helper: rebased onto {args.onto} cleanly")
        elif not rebase_in_progress():
            print(proc.stderr.strip() or "rebase-helper: rebase failed before any conflict")
            return 1

    # Conflict loop: resolve mechanical paths, stop on anything else.
    stalls = 0
    while rebase_in_progress():
        paths = unmerged_paths()
        if paths:
            stalls = 0
            other = [p for p in paths if not is_mechanical(p)]
            if other:
                return report_blocked(other, [p for p in paths if is_mechanical(p)])
            resolve_mechanical(paths)
            for p in paths:
                print(f"rebase-helper: resolved {p} mechanically")
        proc = git("-c", "core.editor=true", "rebase", "--continue", check=False)
        if proc.returncode != 0 and rebase_in_progress() and not unmerged_paths():
            # --continue failed without leaving conflicts (empty commit,
            # missing editor, ...): bail rather than loop forever.
            stalls += 1
            if stalls >= 2:
                print("rebase-helper: `git rebase --continue` keeps failing with no conflicts left:")
                print(git("status").stdout)
                return 1

    if rebase_in_progress():
        return 1
    if git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "HEAD":
        print("rebase-helper: HEAD is detached after the rebase; check `git status` before continuing")
        return 1

    print("rebase-helper: regenerating census pins and support.json")
    if regenerate() != 0:
        print("rebase-helper: regeneration failed; fix and re-run")
        return 1
    drop_metadata_only_diff(SUPPORT_JSON)
    dirty = dirty_paths()
    if dirty:
        print("rebase-helper: regeneration changed files; review and commit them:")
        for p in dirty:
            print(f"  {p}")
        print("suggested: git add -A && git commit -m 'chore: regenerate census pins and support table after rebase'")
        return 2
    print("rebase-helper: done, tree clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
