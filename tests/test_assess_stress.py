"""`pyscriptc coverage` and `pyscriptc assess` survive a real package tree.

Issue #730: one file the lowering could not read or parse (a syntax error,
a null byte, a non-UTF-8 encoding, a name that is not a module name, a
FIFO) ended the whole run with a traceback and a nonzero exit. Each shape
below reproduces one of those crashes on a tiny tree: the commands must
complete, name the file and the reason in their summary, and exit 0.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from pyscriptc import analyze, assess, cli, coverage

OK = "def ok() -> int:\n    return 1\n"


def make_tree(root: Path, files: dict[str, str | bytes]) -> Path:
    """A package tree with the given files, one healthy module beside them."""

    pkg = root / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            path.write_bytes(body)
        else:
            path.write_text(body, encoding="utf-8")
    (pkg / "ok.py").write_text(OK, encoding="utf-8")
    return root


def coverage_json(root: Path) -> dict:
    report = coverage.build_report(root)
    return report


def issues_of(report: dict) -> dict[str, str]:
    return {item["file"]: item["reason"] for item in report.get("issues", [])}


# ---------------------------------------------------------------------------
# The scanner: what it reads, and what it refuses


def test_scan_reads_a_healthy_file(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {})
    files, failures = analyze.scan(root)
    assert failures == []
    assert [path.name for path in files] == ["ok.py"]


def test_scan_reports_a_syntax_error(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bad.py": "def broken(:\n    pass\n"})
    files, failures = analyze.scan(root)
    assert [path.name for path in files] == ["ok.py"]
    assert len(failures) == 1
    assert "syntax error" in failures[0].reason


def test_scan_reports_a_null_byte(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bad.py": b"def f():\n    return 1\n\x00\n"})
    _files, failures = analyze.scan(root)
    assert len(failures) == 1
    assert "null byte" in failures[0].reason


def test_scan_reports_an_undecodable_file(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bad.py": b"def f():\n    return '\xff\xfe'\n"})
    _files, failures = analyze.scan(root)
    assert len(failures) == 1
    assert "UTF-8" in failures[0].reason


def test_scan_honours_a_declared_encoding(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/latin.py": "# -*- coding: latin-1 -*-\ns = '\xe9'\n"})
    files, failures = analyze.scan(root)
    assert failures == []
    assert sorted(path.name for path in files) == ["latin.py", "ok.py"]


def test_scan_reports_an_unknown_declared_encoding(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bad.py": b"# -*- coding: no-such-codec -*-\nx = 1\n"})
    _files, failures = analyze.scan(root)
    assert len(failures) == 1
    assert "unknown encoding declaration" in failures[0].reason


def test_scan_strips_a_utf8_bom(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bom.py": b"\xef\xbb\xbf" + OK.encode()})
    files, failures = analyze.scan(root)
    assert failures == []
    assert sorted(path.name for path in files) == ["bom.py", "ok.py"]


def test_scan_refuses_a_fifo_without_reading_it(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {})
    os.mkfifo(root / "pkg" / "pipe.py")
    files, failures = analyze.scan(root)
    assert [path.name for path in files] == ["ok.py"]
    assert len(failures) == 1
    assert "not a regular file" in failures[0].reason


def test_scan_reports_a_directory_named_like_a_module(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {})
    (root / "pkg" / "mod.py").mkdir()
    files, failures = analyze.scan(root)
    assert [path.name for path in files] == ["ok.py"]
    assert len(failures) == 1
    assert "directory" in failures[0].reason


def test_scan_follows_no_symlink_loop(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {})
    inner = root / "pkg" / "inner"
    inner.mkdir()
    os.symlink(inner, inner / "self")
    files, failures = analyze.scan(root)
    assert failures == []
    assert [path.name for path in files] == ["ok.py"]


def test_scan_reports_a_broken_symlink(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {})
    os.symlink(root / "pkg" / "missing.py", root / "pkg" / "gone.py")
    files, failures = analyze.scan(root)
    assert [path.name for path in files] == ["ok.py"]
    assert len(failures) == 1
    assert "does not exist" in failures[0].reason


def test_scan_reports_an_unreadable_file(tmp_path: Path) -> None:
    if os.getuid() == 0:
        pytest.skip("root reads every file")
    root = make_tree(tmp_path / "t", {"pkg/secret.py": OK})
    (root / "pkg" / "secret.py").chmod(0)
    try:
        _files, failures = analyze.scan(root)
    finally:
        (root / "pkg" / "secret.py").chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert len(failures) == 1
    assert "cannot read file" in failures[0].reason


def test_scan_reports_a_deeply_nested_expression(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/deep.py": "x = " + "(" * 300 + "1" + ")" * 300 + "\n"})
    _files, failures = analyze.scan(root)
    assert len(failures) == 1
    assert "syntax error" in failures[0].reason


# ---------------------------------------------------------------------------
# `pyscriptc coverage`


def test_coverage_completes_on_a_tree_with_a_syntax_error(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bad.py": "def broken(:\n    pass\n"})
    report = coverage_json(root)
    issues = issues_of(report)
    assert len(issues) == 1
    assert "syntax error" in next(iter(issues.values()))
    assert report["summary"]["definitions"] == 1


def test_coverage_completes_on_a_tree_with_a_null_byte(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bad.py": b"def f():\n    return 1\n\x00\n"})
    report = coverage_json(root)
    assert len(issues_of(report)) == 1


def test_coverage_completes_on_a_tree_with_a_bad_encoding(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bad.py": b"def f():\n    return '\xff\xfe'\n"})
    report = coverage_json(root)
    assert len(issues_of(report)) == 1


def test_coverage_completes_on_a_tree_with_a_fifo(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {})
    os.mkfifo(root / "pkg" / "pipe.py")
    report = coverage_json(root)
    assert len(issues_of(report)) == 1


def test_coverage_completes_on_a_tree_with_a_non_module_name(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/not-an-identifier.py": OK})
    report = coverage_json(root)
    # The file is not a module, so it is not a failure: it is skipped.
    assert issues_of(report) == {}


def test_coverage_names_the_broken_file_in_its_text(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bad.py": "def broken(:\n    pass\n"})
    text = coverage.render_text(coverage_json(root))
    assert "files that could not be analyzed" in text
    assert "bad.py" in text


def test_coverage_json_carries_the_failures(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bad.py": "def broken(:\n    pass\n"})
    report = coverage_json(root)
    assert report["issues"]
    assert set(report["issues"][0]) == {"file", "reason"}


# ---------------------------------------------------------------------------
# `pyscriptc assess`


def test_assess_completes_on_a_tree_with_a_syntax_error(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bad.py": "def broken(:\n    pass\n"})
    report = assess.assess(root)
    issues = {item["file"]: item["reason"] for item in report["analysisIssues"]}
    assert len(issues) == 1
    assert "syntax error" in next(iter(issues.values()))
    # The healthy file still lowers.
    assert any(item["file"] == "pkg/ok.py" and item["status"] == "fully" for item in report["files"])


def test_assess_completes_on_a_tree_with_a_null_byte(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bad.py": b"def f():\n    return 1\n\x00\n"})
    report = assess.assess(root)
    assert len(report["analysisIssues"]) == 1


def test_assess_completes_on_a_tree_with_a_bad_encoding(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bad.py": b"def f():\n    return '\xff\xfe'\n"})
    report = assess.assess(root)
    assert len(report["analysisIssues"]) == 1


def test_assess_completes_on_a_tree_with_a_fifo(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {})
    os.mkfifo(root / "pkg" / "pipe.py")
    report = assess.assess(root)
    assert len(report["analysisIssues"]) == 1


def test_assess_names_the_broken_file_in_its_text(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bad.py": "def broken(:\n    pass\n"})
    text = assess.render_text(assess.assess(root))
    assert "Files that could not be analyzed" in text
    assert "bad.py" in text


def test_assess_does_not_mark_every_file_refused_for_one_broken_file(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bad.py": "def broken(:\n    pass\n"})
    report = assess.assess(root)
    statuses = {item["file"]: item["status"] for item in report["files"]}
    assert statuses["pkg/ok.py"] == "fully"
    assert statuses["pkg/bad.py"] != "fully"


# ---------------------------------------------------------------------------
# The commands themselves


def test_cli_coverage_exits_zero_on_a_broken_tree(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bad.py": "def broken(:\n    pass\n"})
    code = cli.main(["coverage", str(root)])
    assert code == 0


def test_cli_coverage_json_exits_zero_on_a_broken_tree(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bad.py": "def broken(:\n    pass\n"})
    code = cli.main(["coverage", str(root), "--json"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["issues"]


def test_cli_assess_exits_zero_on_a_broken_tree(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bad.py": "def broken(:\n    pass\n"})
    code = cli.main(["assess", str(root)])
    assert code == 0


def test_cli_assess_json_exits_zero_on_a_broken_tree(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bad.py": "def broken(:\n    pass\n"})
    code = cli.main(["assess", str(root), "--json"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["analysisIssues"]


def test_cli_imports_exits_zero_on_a_broken_tree(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "t", {"pkg/bad.py": "def broken(:\n    pass\n"})
    code = cli.main(["imports", str(root)])
    assert code == 0


# ---------------------------------------------------------------------------
# The shapes that are not crashes but must not break the analysis


def test_coverage_handles_a_relative_import_cycle(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path / "t",
        {
            "pkg/a.py": "from .b import b\n\n\ndef a() -> int:\n    return b()\n",
            "pkg/b.py": "from .a import a\n\n\ndef b() -> int:\n    return 1\n",
        },
    )
    report = coverage_json(root)
    assert issues_of(report) == {}


def test_coverage_handles_a_namespace_package(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path / "t",
        {
            "ns/mod.py": "from .helper import helper\n\n\ndef run() -> int:\n    return helper()\n",
            "ns/helper.py": OK,
        },
    )
    report = coverage_json(root)
    assert issues_of(report) == {}


def test_assess_handles_a_huge_generated_file(tmp_path: Path) -> None:
    body = "\n".join(f"def f{i}() -> int:\n    return {i}\n" for i in range(2000))
    root = make_tree(tmp_path / "t", {"pkg/big.py": body})
    report = assess.assess(root)
    assert report["analysisIssues"] == []


# ---------------------------------------------------------------------------
# Bounded output


def test_assess_caps_the_plane_map_and_all_prints_it(tmp_path: Path) -> None:
    body = "\n".join(f"def f{i}() -> int:\n    return {i}\n" for i in range(200))
    root = make_tree(tmp_path / "t", {"pkg/big.py": body})
    report = assess.assess(root)
    capped = assess.render_text(report)
    full = assess.render_text(report, show_all=True)
    assert "more definition(s) (use --all for full list)" in capped
    assert "more definition(s)" not in full
    assert len(full.splitlines()) > len(capped.splitlines())


def test_coverage_caps_the_file_list_and_all_prints_it(tmp_path: Path) -> None:
    files = {f"pkg/m{i}.py": "def f() -> int:\n    return 1\n" for i in range(20)}
    root = make_tree(tmp_path / "t", files)
    report = coverage_json(root)
    capped = coverage.render_text(report)
    full = coverage.render_text(report, show_all=True)
    assert "more file(s) (use --all for full list)" in capped
    assert "more file(s)" not in full


def test_cli_coverage_all_prints_the_full_list(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    files = {f"pkg/m{i}.py": "def f() -> int:\n    return 1\n" for i in range(20)}
    root = make_tree(tmp_path / "t", files)
    code = cli.main(["coverage", str(root), "--all"])
    assert code == 0
    assert "more file(s)" not in capsys.readouterr().out
