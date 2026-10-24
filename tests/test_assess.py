"""Tests for `pyscriptc assess PATH`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pyscriptc import assess, cli

needs_cc = pytest.mark.skipif(
    shutil.which("clang") is None and shutil.which("zig") is None,
    reason="clang or zig required",
)

TYPED_SCRIPT = """
def add(a: int, b: int) -> int:
    return a + b


def main() -> int:
    total: int = 0
    for i in range(1000):
        total = add(total, i)
    print(total)
    return 0


if __name__ == "__main__":
    main()
"""

UNTYPED_DEP_SCRIPT = """
import requests


def fetch_data() -> int:
    return 1


if __name__ == "__main__":
    fetch_data()
"""

REFLECTION_SCRIPT = """
import inspect


def examine(x: int) -> int:
    sig = inspect.signature(examine)
    return x


if __name__ == "__main__":
    examine(10)
"""

MIXED_LIBRARY_A = """
def pure_calc(x: int, y: int) -> int:
    return x * y + 10
"""

MIXED_LIBRARY_B = """
import requests
from .mod_a import pure_calc


def with_union(x: int | str) -> int:
    return pure_calc(1, 2)
"""


@pytest.fixture(autouse=True)
def cache_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "cache"
    monkeypatch.setenv("PYSC_CACHE_DIR", str(home))
    monkeypatch.delenv("PYSC_NO_RUN_CACHE", raising=False)
    return home


def test_assess_single_script_typed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = tmp_path / "typed.py"
    script.write_text(TYPED_SCRIPT, encoding="utf-8")

    code = cli.main(["assess", str(script), "--json"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert data["schemaVersion"] == 1
    assert data["summary"]["files"]["fully"] == 1
    assert data["summary"]["shares"]["native"] == 1.0
    assert data["summary"]["shares"]["island"] == 0.0
    assert data["summary"]["shares"]["blocked"] == 0.0
    assert len(data["files"]) == 1
    assert data["files"][0]["status"] == "fully"
    assert data["files"][0]["fences"] == 0
    assert data["islandRouting"]["policyLines"] == []
    assert data["islandRouting"]["staysBlocked"] == []
    # No speed numbers without --bench
    assert data["bench"] is None

    # Text report
    code_text = cli.main(["assess", str(script)])
    assert code_text == 0
    text_out = capsys.readouterr().out
    assert "Native:   100.0%" in text_out
    assert "lowers fully" in text_out
    assert "x CPython" not in text_out


@needs_cc
def test_assess_single_script_with_verify_and_bench(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = tmp_path / "typed_run.py"
    script.write_text(TYPED_SCRIPT, encoding="utf-8")

    code = cli.main(["assess", str(script), "--verify", "--bench", "--json"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)

    # Verify check
    assert data["verify"] is not None
    assert len(data["verify"]) == 1
    receipt = data["verify"][0]
    assert receipt["match"] is True

    # Bench check
    assert data["bench"] is not None
    assert len(data["bench"]) == 1
    bench = data["bench"][0]
    assert bench["samples"] == 5
    assert bench["ratio"] > 0
    assert "x CPython" in bench["speedup"]

    # Text rendering contains inline verify and bench
    code_text = cli.main(["assess", str(script), "--verify", "--bench"])
    assert code_text == 0
    text_out = capsys.readouterr().out
    assert "Verify:" in text_out
    assert "match" in text_out
    assert "Benchmark:" in text_out
    assert "x CPython" in text_out


def test_assess_untyped_dependency_island_route(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = tmp_path / "dep.py"
    script.write_text(UNTYPED_DEP_SCRIPT, encoding="utf-8")

    code = cli.main(["assess", str(script), "--json"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert data["summary"]["files"]["partly"] == 1
    f_res = data["files"][0]
    assert f_res["status"] == "partly"
    assert len(f_res["groupedFences"]["untyped-import"]) == 1
    untyped = f_res["groupedFences"]["untyped-import"][0]
    assert untyped["importName"] == "requests"

    assert 'requests = "island"' in data["islandRouting"]["policyLines"]
    assert any(r["name"] == "requests" for r in data["islandRouting"]["routes"])

    # Human text output
    cli.main(["assess", str(script)])
    text_out = capsys.readouterr().out
    assert 'requests = "island"' in text_out
    assert 'island route: requests = "island"' in text_out


def test_assess_reflection_culprits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = tmp_path / "refl.py"
    script.write_text(REFLECTION_SCRIPT, encoding="utf-8")

    code = cli.main(["assess", str(script), "--json"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)

    f_res = data["files"][0]
    refl_fences = f_res["groupedFences"]["reflection"]
    assert len(refl_fences) >= 1
    assert refl_fences[0]["culprit"] == "inspect.signature"
    assert "declare the parameters explicitly" in refl_fences[0]["typedIdiom"]
    assert data["summary"]["definitions"]["island"] == 1


def test_assess_real_app_directory(capsys: pytest.CaptureFixture[str]) -> None:
    jwtapp_path = Path("benchmarks/compat/pyjwt/src/jwtapp")
    assert jwtapp_path.is_dir()

    code = cli.main(["assess", str(jwtapp_path), "--json"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert data["schemaVersion"] == 1
    assert data["summary"]["files"]["total"] == 3
    assert data["summary"]["files"]["fully"] == 3
    assert data["summary"]["shares"]["native"] == 1.0
    assert len(data["files"]) == 3
    assert any(f["file"] == "main.py" for f in data["files"])
    assert any(f["file"] == "jwt_compat.py" for f in data["files"])


def test_assess_pure_python_library_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    lib_dir = tmp_path / "mylib"
    lib_dir.mkdir()
    (lib_dir / "__init__.py").write_text("", encoding="utf-8")
    (lib_dir / "mod_a.py").write_text(MIXED_LIBRARY_A, encoding="utf-8")
    (lib_dir / "mod_b.py").write_text(MIXED_LIBRARY_B, encoding="utf-8")

    code = cli.main(["assess", str(lib_dir), "--json"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert data["summary"]["files"]["total"] == 3
    # mod_a lowers fully, mod_b is partly or refused
    mod_a = next(f for f in data["files"] if f["file"] == "mod_a.py")
    assert mod_a["status"] == "fully"

    mod_b = next(f for f in data["files"] if f["file"] == "mod_b.py")
    assert mod_b["status"] in ("partly", "refused")
    assert len(mod_b["groupedFences"]["untyped-import"]) >= 1
    assert len(mod_b["groupedFences"]["unsupported-construct"]) >= 1

    # Stays blocked should contain the union type fence
    assert any("union" in blk["family"] or "union" in blk["message"] for blk in data["islandRouting"]["staysBlocked"])


def test_assess_json_schema_validation(tmp_path: Path) -> None:
    script = tmp_path / "check.py"
    script.write_text(TYPED_SCRIPT, encoding="utf-8")

    report = assess.assess(script)

    # Validate mandatory schema fields
    assert "schemaVersion" in report and isinstance(report["schemaVersion"], int)
    assert "target" in report and isinstance(report["target"], str)
    assert "summary" in report and isinstance(report["summary"], dict)

    summary = report["summary"]
    for key in ("files", "definitions", "shares", "imports", "requiresLibpython"):
        assert key in summary

    for share_key in ("native", "island", "blocked"):
        assert share_key in summary["shares"]
        assert 0.0 <= summary["shares"][share_key] <= 1.0

    assert "files" in report and isinstance(report["files"], list)
    for f in report["files"]:
        assert "file" in f
        assert f["status"] in ("fully", "partly", "refused")
        assert "definitions" in f
        assert "groupedFences" in f
        for group in ("reflection", "untyped-import", "unsupported-construct"):
            assert group in f["groupedFences"]

    assert "imports" in report and isinstance(report["imports"], list)
    assert "islandRouting" in report and isinstance(report["islandRouting"], dict)
    assert "policyLines" in report["islandRouting"]
    assert "routes" in report["islandRouting"]
    assert "staysBlocked" in report["islandRouting"]


def test_assess_graceful_on_syntax_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad_script = tmp_path / "bad.py"
    bad_script.write_text("def broken(:\n", encoding="utf-8")

    # Assess should not crash, but return a report marking it refused or noting error
    code = cli.main(["assess", str(bad_script), "--json"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["summary"]["files"]["total"] >= 1
    assert data["summary"]["shares"]["native"] == 0.0
