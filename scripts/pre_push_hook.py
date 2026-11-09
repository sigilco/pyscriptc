"""Fast pre-push checks: the cheap gates CI would catch anyway.

Runs in well under a minute and never runs the test suite. Installed by
`mise run hooks:install` (git core.hooksPath -> scripts/hooks); the
`scripts/hooks/pre-push` wrapper feeds it the pre-push stdin protocol.

Checks, all scoped to the files the push touches:

- conflict markers in any changed file
- `ruff check` and `ruff format --check` (whole tree, same excludes as CI)
  when Python or ruff config changed
- `scripts/gen_support_table.py --check` when its inputs changed
- `tests/test_census_self.py` when the compiler tree or the pins changed
- em-dash and absolute-home-path scan on added lines of changed markdown

`--no-verify` on `git push` skips the hook entirely. `mise run check:fast`
runs the same checks against uncommitted working-tree changes.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RUFF_FORMAT_EXCLUDES = [
    "src/pyscriptc",
    "benchmarks/compat/score.py",
    ".agents/docs/loop.md",
    "benchmarks/runtime/workloads/pow_loop.py",
    "benchmarks/runtime/workloads/tuple_membership.py",
    "benchmarks/compat2/bundle.py",
    "benchmarks/compat2/cache.py",
    "benchmarks/compat2/corpus/spec.py",
    "benchmarks/compat2/coverage_map.py",
    "benchmarks/compat2/score.py",
]

# Inputs of scripts/gen_support_table.py --check (see its module docstring).
SUPPORT_INPUTS = (
    "src/",
    "scripts/support_data.toml",
    ".agents/docs/support/",
    "data/compat/",
)

CENSUS_INPUTS = ("src/pyscriptc/", "tests/census_pins")

MARKDOWN = (".md", ".markdown", ".mdx")
CONFLICT = re.compile(r"^(<{7}|>{7})( |$)|^={7}$")
HOME_PATH = re.compile(r"/(Users|home)/[^\s`'\")]+")
EM_DASH = "—"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def pushed_files() -> tuple[list[str], str | None]:
    """Files changed by the refs on stdin; returns (files, diff_base)."""
    lines = [ln.split() for ln in sys.stdin if ln.strip()]
    files: set[str] = set()
    base: str | None = None
    for parts in lines:
        if len(parts) < 4:
            continue
        _local_ref, local_sha, _remote_ref, remote_sha = parts[:4]
        if set(local_sha) == {"0"}:
            continue  # branch deletion
        if remote_sha and not set(remote_sha) == {"0"} and git_ok("cat-file", "-e", remote_sha):
            start = remote_sha
        else:
            # New branch or rewritten history: diff from the merge base
            # with the default branch, which is the conservative superset.
            upstream = "origin/master"
            if not git_ok("rev-parse", "--verify", upstream):
                upstream = "HEAD"
            mb = git("merge-base", upstream, local_sha).strip()
            start = mb or local_sha + "~1"
            if base is None:
                base = start
        out = git("diff", "--name-only", start, local_sha)
        files.update(ln for ln in out.splitlines() if ln)
    return sorted(files), base


def git_ok(*args: str) -> bool:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True).returncode == 0


def working_tree_files() -> tuple[list[str], str | None]:
    """check:fast mode: uncommitted + untracked changes vs HEAD."""
    out = git("status", "--porcelain")
    files = []
    for ln in out.splitlines():
        path = ln[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append(path.strip('"'))
    return sorted(set(files)), "HEAD"


def uv_run(*args: str) -> int:
    return subprocess.run(["uv", "run", *args], cwd=ROOT).returncode


def check_markdown(files: list[str], base: str | None) -> int:
    """Em-dash and home-path scan on added lines of changed markdown."""
    bad = 0
    for path in files:
        if not path.endswith(MARKDOWN):
            continue
        full = ROOT / path
        if not full.is_file():
            continue
        diff = ""
        if base:
            diff = git("diff", "-U0", base, "--", path)
        added = [ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
        # Untracked files have no diff; scan the whole file.
        if not diff and not git_ok("ls-files", "--error-unmatch", path):
            added = full.read_text(encoding="utf-8", errors="replace").splitlines()
        for ln in added:
            if EM_DASH in ln:
                print(f"::error file={path}::em-dash (U+2014) in added line: {ln.strip()[:100]}")
                bad += 1
            if HOME_PATH.search(ln):
                print(f"::error file={path}::absolute home path in added line: {ln.strip()[:100]}")
                bad += 1
    return bad


def main() -> int:
    check_mode = "--check" in sys.argv[1:]
    if check_mode:
        files, base = working_tree_files()
    else:
        files, base = pushed_files()
    if not files:
        print("pre-push: no changed files, nothing to check")
        return 0
    print(f"pre-push: checking {len(files)} changed file(s)")

    failures = 0

    # 1. Conflict markers in changed text files.
    for path in files:
        full = ROOT / path
        if not full.is_file() or full.stat().st_size > 2_000_000:
            continue
        try:
            text = full.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError, OSError:
            continue
        for i, ln in enumerate(text.splitlines(), 1):
            if CONFLICT.match(ln):
                print(f"::error file={path},line={i}::conflict marker: {ln.strip()}")
                failures += 1
                break

    py_changed = any(p.endswith(".py") for p in files) or any(
        p in ("pyproject.toml", "ruff.toml", ".ruff.toml") for p in files
    )

    # 2. ruff, gated on Python/config changes.
    if py_changed:
        if uv_run("ruff", "check") != 0:
            failures += 1
        rc = subprocess.run(
            [
                "uv",
                "run",
                "ruff",
                "format",
                "--check",
                *[f"--exclude={e}" for e in RUFF_FORMAT_EXCLUDES],
            ],
            cwd=ROOT,
        ).returncode
        if rc != 0:
            failures += 1

    # 3. Support table regeneration check.
    if any(p.startswith(SUPPORT_INPUTS) for p in files):
        if uv_run("python", "scripts/gen_support_table.py", "--check") != 0:
            failures += 1

    # 4. Census pin consistency (the cheap per-module ceiling test). It
    # lowers the whole compiler tree, so it costs minutes, not seconds:
    # opt in with PYSC_HOOK_CENSUS=1. The CI shards always run it.
    if any(p.startswith(CENSUS_INPUTS) for p in files):
        if os.environ.get("PYSC_HOOK_CENSUS") == "1":
            if uv_run("pytest", "-q", "tests/test_census_self.py") != 0:
                failures += 1
        else:
            print("pre-push: census pin check skipped (set PYSC_HOOK_CENSUS=1 to run it; CI shards cover it)")

    # 5. Markdown scans.
    failures += check_markdown(files, base)

    if failures:
        print(f"pre-push: {failures} check(s) failed (skip with --no-verify)")
        return 1
    print("pre-push: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
