"""boxed_cost.py on synthetic git histories."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from benchmarks.compat2 import boxed_cost

BOXED = "src/pyscriptc/boxed.py"
NATIVE = "src/pyscriptc/lower.py"


def _git(repo: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


class Repo:
    def __init__(self, path: Path) -> None:
        self.path = path
        _git(path, "init", "-q")
        self.n = 0

    def commit(self, subject: str, **files: str) -> None:
        """files maps a path (with / replaced by __) to text appended to that file."""
        for key, text in files.items():
            target = self.path / key.replace("__", "/")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(text)
        self.n += 1
        _git(self.path, "add", "-A")
        _git(self.path, "commit", "-q", "-m", subject)


def _lines(n: int, tag: str = "x") -> str:
    return "".join(f"{tag} = {i}\n" for i in range(n))


def test_no_boxed_code_is_all_zeros(tmp_path: Path) -> None:
    repo = Repo(tmp_path)
    for i in range(7):
        repo.commit(f"feat: native {i}", **{NATIVE.replace("/", "__"): _lines(5, f"n{i}")})
    report = boxed_cost.build_report(tmp_path, size_commits=3)
    assert report.drop_flag is False
    assert all(w.boxed_churn == 0 and w.code_share == 0 for w in report.windows)


def _history(tmp_path: Path, boxed_heavy_windows: int) -> Repo:
    """Windows of 4 commits, oldest first. The newest `boxed_heavy_windows` are boxed heavy."""
    repo = Repo(tmp_path)
    total = 3
    for window in range(total):
        heavy = window >= total - boxed_heavy_windows
        for i in range(4):
            if heavy and i < 2:
                repo.commit(
                    f"fix: patch {window}.{i}" if i == 0 else f"feat: lower {window}.{i}",
                    **{BOXED.replace("/", "__"): _lines(30, f"b{window}{i}")},
                )
            else:
                repo.commit(
                    f"feat: native {window}.{i}",
                    **{NATIVE.replace("/", "__"): _lines(10, f"n{window}{i}")},
                )
    return repo


def test_two_heavy_windows_raise_the_flag(tmp_path: Path) -> None:
    _history(tmp_path, boxed_heavy_windows=2)
    report = boxed_cost.build_report(tmp_path, size_commits=4)
    assert [w.commits for w in report.windows] == [4, 4, 4]
    assert report.windows[0].churn_share > boxed_cost.CHURN_LIMIT
    assert report.drop_flag is True
    assert any("two consecutive" in r for r in report.reasons)
    # a boxed commit that does not name boxed, island or fallback is iteration tax
    assert report.windows[0].tax_commits == 2
    assert any("iteration_tax" in r for r in report.reasons)


def test_one_heavy_window_alone_does_not_trip_the_churn_rule(tmp_path: Path) -> None:
    _history(tmp_path, boxed_heavy_windows=1)
    report = boxed_cost.build_report(tmp_path, size_commits=4)
    assert not any("two consecutive" in r for r in report.reasons)


def test_named_boxed_commits_are_not_tax(tmp_path: Path) -> None:
    repo = Repo(tmp_path)
    for i in range(4):
        repo.commit(f"feat(boxed): box {i}", **{BOXED.replace("/", "__"): _lines(5, f"v{i}")})
    report = boxed_cost.build_report(tmp_path, size_commits=4)
    assert report.windows[0].iteration_tax == 0


def test_marker_regions_count_as_boxed_code(tmp_path: Path) -> None:
    repo = Repo(tmp_path)
    body = "a = 1\n# boxed:begin\nb = 2\nc = 3\n# boxed:end\nd = 4\n"
    repo.commit("feat: native with a guarded branch", **{NATIVE.replace("/", "__"): body})
    report = boxed_cost.build_report(tmp_path, size_commits=5)
    assert report.windows[0].boxed_lines == 4
    assert report.windows[0].compiler_lines == 6
    assert report.windows[0].tax_commits == 1  # the commit touches a marker without naming boxed


def test_flat_reach_with_rising_cost_raises_the_flag(tmp_path: Path) -> None:
    _history(tmp_path, boxed_heavy_windows=1)
    report = boxed_cost.build_report(tmp_path, size_commits=4, reach=[5, 5, 5])
    assert any("reach" in r for r in report.reasons)
    rising = boxed_cost.build_report(tmp_path, size_commits=4, reach=[3, 5, 8])
    assert not any("reach" in r for r in rising.reasons)


def test_cli_prints_json_report(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    repo = Repo(tmp_path)
    repo.commit("feat: native", **{NATIVE.replace("/", "__"): _lines(3)})
    reach_file = tmp_path / "reach.json"
    reach_file.write_text(json.dumps({"boxed_only_reach": [1, 2, 3]}))
    assert boxed_cost.main(["--repo", str(tmp_path), "--json", "--reach", str(reach_file)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["drop_flag"] is False
    assert payload["windows"][0]["commits"] == 1
