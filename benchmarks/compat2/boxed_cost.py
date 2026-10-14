"""Maintenance cost ledger and drop flag for the opt-in boxed fallback.

Reads git history only. Works today with no boxed code (all zeros, flag false).

Boxed code is:
  * every file matching BOXED_PATHS (globs relative to the repo root), and
  * any line between `boxed:begin` and `boxed:end` marker comments in other
    compiler files, for example `# boxed:begin` ... `# boxed:end`.

Windows are consecutive slices of the first-parent history, newest first, sized
by --window-commits or --window-days. Metrics per window:

  code_share    boxed lines / compiler lines, measured at the window's newest commit
  churn_share   boxed changed lines / compiler changed lines
  bug_share     `fix` commits touching boxed code / all `fix` commits
  iteration_tax commits that touch boxed code without naming it (subject lacks
                boxed, island or fallback) / all commits

Drop flag rules (any one raises it):
  1. churn_share > 0.20 or bug_share > 0.25 in each of the two newest windows
  2. iteration_tax > 0.15 in the newest window
  3. boxed-only reach (from --reach) did not rise across the three newest
     windows while the cost score (churn_share + bug_share) rose
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

BOXED_PATHS = ("src/pyscriptc/boxed.py", "src/pyscriptc/runtime/ps_island*")
COMPILER_ROOT = "src/pyscriptc/"
COMPILER_SUFFIXES = (".py", ".c", ".h")
MARK_BEGIN = re.compile(r"boxed:begin")
MARK_END = re.compile(r"boxed:end")
NAMED_AS_BOXED = re.compile(r"boxed|island|fallback", re.IGNORECASE)
FIX = re.compile(r"^fix(\(|:|!)", re.IGNORECASE)

CHURN_LIMIT = 0.20
BUG_LIMIT = 0.25
TAX_LIMIT = 0.15


@dataclass
class Window:
    newest: str
    oldest: str
    commits: int = 0
    code_share: float = 0.0
    churn_share: float = 0.0
    bug_share: float = 0.0
    iteration_tax: float = 0.0
    fixes: int = 0
    boxed_fixes: int = 0
    tax_commits: int = 0
    boxed_lines: int = 0
    compiler_lines: int = 0
    boxed_churn: int = 0
    compiler_churn: int = 0
    boxed_probe_time_share: float | None = None


@dataclass
class Report:
    windows: list[Window] = field(default_factory=list)
    drop_flag: bool = False
    reasons: list[str] = field(default_factory=list)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def is_boxed_path(path: str, boxed_paths: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in boxed_paths)


def is_compiler_file(path: str) -> bool:
    return path.startswith(COMPILER_ROOT) and path.endswith(COMPILER_SUFFIXES)


def count_lines(repo: Path, rev: str, boxed_paths: tuple[str, ...]) -> tuple[int, int]:
    """Return (boxed lines, compiler lines) of the tree at rev."""
    boxed = total = 0
    for path in git(repo, "ls-tree", "-r", "--name-only", rev).splitlines():
        if not is_compiler_file(path):
            continue
        text = git(repo, "show", f"{rev}:{path}")
        lines = text.splitlines()
        total += len(lines)
        if is_boxed_path(path, boxed_paths):
            boxed += len(lines)
            continue
        inside = False
        for line in lines:
            if MARK_BEGIN.search(line):
                inside = True
            if inside:
                boxed += 1
            if MARK_END.search(line):
                inside = False
    return boxed, total


def history(repo: Path) -> list[tuple[str, int, str]]:
    """First-parent commits, newest first: (sha, unix time, subject)."""
    out = git(repo, "log", "--first-parent", "--format=%H%x09%ct%x09%s")
    rows = []
    for line in out.splitlines():
        sha, ts, subject = line.split("\t", 2)
        rows.append((sha, int(ts), subject))
    return rows


def slice_windows(
    commits: list[tuple[str, int, str]], size_commits: int | None, size_days: int | None
) -> list[list[tuple[str, int, str]]]:
    if not commits:
        return []
    if size_commits:
        return [commits[i : i + size_commits] for i in range(0, len(commits), size_commits)]
    assert size_days
    span = size_days * 86400
    top = commits[0][1]
    windows: dict[int, list[tuple[str, int, str]]] = {}
    for row in commits:
        windows.setdefault((top - row[1]) // span, []).append(row)
    return [windows[k] for k in sorted(windows)]


def commit_files(repo: Path, sha: str, boxed_paths: tuple[str, ...]) -> dict[str, int]:
    """Changed lines per compiler file in one commit, plus a marker hit as 0 lines."""
    out = git(repo, "show", "--first-parent", "--numstat", "--format=", sha)
    files: dict[str, int] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or not is_compiler_file(parts[2]):
            continue
        added, deleted, path = parts
        files[path] = 0 if added == "-" else int(added) + int(deleted)
    return files


def touches_marker(repo: Path, sha: str) -> bool:
    patch = git(repo, "show", "--first-parent", "-U0", "--format=", sha, "--", COMPILER_ROOT)
    return any(line[:1] in "+-" and (MARK_BEGIN.search(line) or MARK_END.search(line)) for line in patch.splitlines())


def measure_window(
    repo: Path,
    rows: list[tuple[str, int, str]],
    boxed_paths: tuple[str, ...],
    probe_times: dict[str, float] | None,
) -> Window:
    win = Window(newest=rows[0][0][:12], oldest=rows[-1][0][:12], commits=len(rows))
    win.boxed_lines, win.compiler_lines = count_lines(repo, rows[0][0], boxed_paths)
    for sha, _ts, subject in rows:
        files = commit_files(repo, sha, boxed_paths)
        boxed_hit = any(is_boxed_path(p, boxed_paths) for p in files) or touches_marker(repo, sha)
        win.compiler_churn += sum(files.values())
        win.boxed_churn += sum(n for p, n in files.items() if is_boxed_path(p, boxed_paths))
        if FIX.match(subject):
            win.fixes += 1
            win.boxed_fixes += boxed_hit
        if boxed_hit and not NAMED_AS_BOXED.search(subject):
            win.tax_commits += 1
    if win.compiler_lines:
        win.code_share = win.boxed_lines / win.compiler_lines
    if win.compiler_churn:
        win.churn_share = win.boxed_churn / win.compiler_churn
    if win.fixes:
        win.bug_share = win.boxed_fixes / win.fixes
    win.iteration_tax = win.tax_commits / win.commits
    if probe_times and probe_times.get("total_seconds"):
        win.boxed_probe_time_share = probe_times.get("boxed_seconds", 0.0) / probe_times["total_seconds"]
    return win


def evaluate(windows: list[Window], reach: list[float] | None) -> tuple[bool, list[str]]:
    """windows are newest first; reach is oldest first, one value per window."""
    reasons: list[str] = []
    if len(windows) >= 2 and all(w.churn_share > CHURN_LIMIT or w.bug_share > BUG_LIMIT for w in windows[:2]):
        reasons.append(f"churn_share>{CHURN_LIMIT:.0%} or bug_share>{BUG_LIMIT:.0%} in two consecutive windows")
    if windows and windows[0].iteration_tax > TAX_LIMIT:
        reasons.append(f"iteration_tax {windows[0].iteration_tax:.0%} > {TAX_LIMIT:.0%}")
    if reach and len(reach) >= 3 and len(windows) >= 3:
        r_old, r_mid, r_new = reach[-3:]
        cost = [w.churn_share + w.bug_share for w in reversed(windows[:3])]  # oldest first
        if r_mid <= r_old and r_new <= r_mid and cost[-1] > cost[0]:
            reasons.append("boxed-only reach flat or falling for three windows while cost rises")
    return bool(reasons), reasons


def build_report(
    repo: Path,
    boxed_paths: tuple[str, ...] = BOXED_PATHS,
    size_commits: int | None = 10,
    size_days: int | None = None,
    max_windows: int = 3,
    reach: list[float] | None = None,
    probe_times: dict[str, float] | None = None,
) -> Report:
    slices = slice_windows(history(repo), size_commits, size_days)[:max_windows]
    windows = [
        measure_window(repo, rows, boxed_paths, probe_times if i == 0 else None) for i, rows in enumerate(slices)
    ]
    flag, reasons = evaluate(windows, reach)
    return Report(windows=windows, drop_flag=flag, reasons=reasons)


def summary(report: Report) -> str:
    lines = [f"drop_flag: {report.drop_flag}"]
    lines += [f"  reason: {r}" for r in report.reasons]
    for i, w in enumerate(report.windows):
        lines.append(
            f"window {i} ({w.oldest}..{w.newest}, {w.commits} commits): "
            f"code {w.code_share:.1%}, churn {w.churn_share:.1%}, "
            f"bugs {w.bug_share:.1%} ({w.boxed_fixes}/{w.fixes}), tax {w.iteration_tax:.1%}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=".")
    parser.add_argument("--window-commits", type=int, default=10)
    parser.add_argument("--window-days", type=int, help="use time windows instead of commit windows")
    parser.add_argument("--windows", type=int, default=3, help="how many windows to evaluate")
    parser.add_argument("--boxed-path", action="append", help="glob; repeatable, replaces the defaults")
    parser.add_argument("--reach", help='JSON file: {"boxed_only_reach": [oldest, ..., newest]}')
    parser.add_argument("--probe-times", help='JSON file: {"boxed_seconds": n, "total_seconds": n}')
    parser.add_argument("--json", action="store_true", help="print only the JSON report")
    args = parser.parse_args(argv)

    reach = json.loads(Path(args.reach).read_text())["boxed_only_reach"] if args.reach else None
    probe_times = json.loads(Path(args.probe_times).read_text()) if args.probe_times else None
    report = build_report(
        Path(args.repo),
        tuple(args.boxed_path) if args.boxed_path else BOXED_PATHS,
        None if args.window_days else args.window_commits,
        args.window_days,
        args.windows,
        reach,
        probe_times,
    )
    payload = {"drop_flag": report.drop_flag, "reasons": report.reasons, "windows": [asdict(w) for w in report.windows]}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(summary(report))
        print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
