"""The script corpus: every script parses, runs, and analyses."""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import pytest
from benchmarks.scripts_corpus.oracle import INVOCATIONS, fixtures_dir
from benchmarks.scripts_corpus.run import analyse_all, family_ranking, render_report, script_dirs

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "benchmarks" / "scripts_corpus" / "scripts"


@pytest.fixture(scope="session", autouse=True)
def _stamped_fixture_mtimes() -> None:
    """Restamp the tree fixture mtimes.

    A git checkout cannot preserve mtimes and `old_files` reads them, so
    the corpus fixtures are restamped to the fixed value before any
    script runs. `make_fixtures.main` writes the same bytes it already
    committed, so the checkout content does not change.
    """

    from benchmarks.scripts_corpus.make_fixtures import main as make_fixtures

    make_fixtures()


def test_every_script_has_an_invocation() -> None:
    slugs = {path.name for path in script_dirs(SCRIPTS)}
    assert slugs == set(INVOCATIONS), f"scripts and invocations differ: {slugs ^ set(INVOCATIONS)}"


def test_every_script_has_meta() -> None:
    for path in script_dirs(SCRIPTS):
        meta = path / "meta.json"
        assert meta.is_file(), f"{path.name} has no meta.json"
        document = json.loads(meta.read_text())
        assert document["license"] == "MIT"
        assert document["origin"].startswith("original implementation")


def test_corpus_size_stays_in_band() -> None:
    count = len(list(script_dirs(SCRIPTS)))
    assert 30 <= count <= 50, f"corpus has {count} scripts, the plan asks for 30 to 50"


def test_every_script_compiles_to_ast() -> None:
    for path in script_dirs(SCRIPTS):
        ast.parse((path / "main.py").read_text(), filename=str(path / "main.py"))


def test_every_script_stays_single_file() -> None:
    for path in script_dirs(SCRIPTS):
        py_files = sorted(item.name for item in path.iterdir() if item.suffix == ".py")
        assert py_files == ["main.py"], f"{path.name} holds {py_files}"


def test_every_script_returns_int_from_main() -> None:
    for path in script_dirs(SCRIPTS):
        tree = ast.parse((path / "main.py").read_text())
        mains = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "main"]
        assert mains, f"{path.name} has no main()"
        assert mains[0].returns is not None, f"{path.name} main() has no return annotation"


def test_every_script_guards_the_entry_point() -> None:
    for path in script_dirs(SCRIPTS):
        source = (path / "main.py").read_text()
        assert '__name__ == "__main__"' in source, f"{path.name} has no main guard"


def test_analysis_covers_every_script() -> None:
    results = analyse_all(SCRIPTS)
    slugs = {result.slug for result in results}
    assert slugs == {path.name for path in script_dirs(SCRIPTS)}


def test_analysis_reports_at_least_one_pass() -> None:
    results = analyse_all(SCRIPTS)
    passed = [result.slug for result in results if result.compile_pass]
    assert passed, "no script in the corpus compiles; the runner is measuring nothing"


def test_analysis_reports_blocking_families_for_fenced_scripts() -> None:
    results = analyse_all(SCRIPTS)
    for result in results:
        if result.compile_pass:
            assert result.top_families == []
        else:
            assert result.top_families, f"{result.slug} is fenced but reports no blocking family"


def test_family_ranking_counts_scripts_not_fences() -> None:
    results = analyse_all(SCRIPTS)
    ranking = family_ranking(results)
    assert ranking, "no family blocks any script"
    fenced = [result for result in results if not result.compile_pass]
    for family, count in ranking:
        blocked = sum(1 for result in fenced if any(name == family for name, _ in result.top_families))
        assert blocked == count


def test_report_renders_both_tables() -> None:
    results = analyse_all(SCRIPTS)
    report = render_report(results)
    assert "| script | compile | fences | top blocking features |" in report
    assert "| family | scripts blocked |" in report


def test_fixtures_are_present() -> None:
    for name in ("lines.txt", "columns.csv", "records.json", "access.log", "bundle.tar"):
        assert (fixtures_dir() / name).is_file(), f"fixture {name} is missing"


@pytest.mark.parametrize("slug", sorted(INVOCATIONS))
def test_script_runs_under_cpython(slug: str, tmp_path: Path) -> None:
    from benchmarks.scripts_corpus.oracle import run_script

    record = run_script(slug, tmp_path)
    assert record["exitCode"] == 0, f"{slug} exited {record['exitCode']}: {record['stderr']}"
    assert record["stdout"], f"{slug} printed nothing"


@pytest.mark.parametrize("slug", sorted(INVOCATIONS))
def test_script_is_deterministic(slug: str, tmp_path: Path) -> None:
    from benchmarks.scripts_corpus.oracle import run_script

    first = run_script(slug, tmp_path)
    second = run_script(slug, tmp_path)
    assert first["stdout"] == second["stdout"]
    assert first["exitCode"] == second["exitCode"]


@pytest.mark.parametrize("slug", sorted(INVOCATIONS))
def test_recorded_oracle_matches_a_fresh_run(slug: str, tmp_path: Path) -> None:
    from benchmarks.scripts_corpus.oracle import run_script

    oracle = ROOT / "benchmarks" / "scripts_corpus" / "oracle"
    recorded_path = oracle / f"{slug}.txt"
    if not recorded_path.is_file():
        pytest.skip(f"{slug} has no recorded oracle yet")
    recorded = json.loads((oracle / f"{slug}.json").read_text())
    fresh = run_script(slug, tmp_path)
    # run_script rewrites the fixtures path to the {fixtures} placeholder,
    # so a recording from any machine stays comparable.
    assert fresh["stdout"] == recorded["stdout"], f"{slug} drifted from its recorded stdout"
    assert fresh["exitCode"] == recorded["exitCode"]


def test_every_script_directory_has_an_oracle() -> None:
    oracle = ROOT / "benchmarks" / "scripts_corpus" / "oracle"
    slugs = {path.name for path in SCRIPTS.iterdir() if path.is_dir()}
    tree_slugs = {path.name for path in (ROOT / "benchmarks" / "scripts_corpus" / "trees").iterdir() if path.is_dir()}
    recorded = {path.stem for path in oracle.glob("*.txt")}
    assert slugs == recorded - tree_slugs, f"scripts and recordings differ: {slugs ^ (recorded - tree_slugs)}"


needs_cc = pytest.mark.skipif(
    shutil.which("clang") is None and shutil.which("zig") is None,
    reason="no C driver (clang or zig) on PATH",
)


@needs_cc
def test_exec_stage_runs_a_compiling_script() -> None:
    """The exec stage builds a binary and compares it with the CPython recording."""

    from benchmarks.scripts_corpus.run import OUTCOME_PASS, OUTCOME_WRONG_OUTPUT, analyse, exec_script

    result = analyse(SCRIPTS / "count_lines")
    assert result.compile_pass, "count_lines is expected to compile"
    exec_script(result, [str(fixtures_dir() / "lines.txt")], ROOT / "benchmarks" / "scripts_corpus" / "oracle")
    assert result.exec_outcome in {OUTCOME_PASS, OUTCOME_WRONG_OUTPUT}
    if result.exec_outcome == OUTCOME_WRONG_OUTPUT:
        # Issue #183: sys.exit(main()) evaluates main() twice, so every
        # compiling script doubles its output. The corpus reports it until
        # the fix lands; the assertion keeps the report honest.
        assert "stdout differs" in result.exec_detail


@needs_cc
def test_exec_stage_skips_a_fenced_script() -> None:
    from benchmarks.scripts_corpus.run import analyse, exec_script

    result = analyse(SCRIPTS / "word_count")
    assert not result.compile_pass
    exec_script(result, [str(fixtures_dir() / "lines.txt")], ROOT / "benchmarks" / "scripts_corpus" / "oracle")
    assert result.exec_outcome == ""


TREES = ROOT / "benchmarks" / "scripts_corpus" / "trees"


def test_every_tree_directory_has_an_oracle() -> None:
    oracle = ROOT / "benchmarks" / "scripts_corpus" / "oracle"
    slugs = {path.name for path in TREES.iterdir() if path.is_dir()}
    recorded = {path.stem for path in oracle.glob("*.txt")}
    assert slugs <= recorded, f"trees without a recording: {slugs - recorded}"
    assert not (slugs & {path.name for path in SCRIPTS.iterdir() if path.is_dir()}), (
        "a tree slug collides with a script slug; the oracle directory is shared"
    )


def test_every_tree_has_a_meta_and_an_entry() -> None:
    for path in TREES.iterdir():
        if not path.is_dir():
            continue
        assert (path / "meta.json").is_file(), path.name
        assert (path / "__main__.py").is_file(), path.name
        meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
        for name in meta["modules"]:
            assert (path / name).is_file(), (path.name, name)


def test_tree_runner_reports_the_corpus() -> None:
    from benchmarks.scripts_corpus.trees import analyse_all, tree_dirs

    results = analyse_all(TREES)
    assert len(results) == len(tree_dirs(TREES))
    for result in results:
        assert result.fences >= 0
        assert result.slug


def test_tree_runner_renders_a_report() -> None:
    from benchmarks.scripts_corpus.trees import analyse_all, render_report

    report = render_report(analyse_all(TREES), with_exec=False)
    assert "R2" in report
    assert "multi-module trees" in report


def test_ladder_document_has_the_three_parts() -> None:
    from benchmarks.scripts_corpus.ladder import build

    document = build(exec_stage=False)
    assert document["schema"] == 1
    rungs = {rung["rung"]: rung for rung in document["rungs"]}
    assert set(rungs) == {"R1", "R2"}
    for rung in rungs.values():
        assert rung["total"] > 0
        assert rung["compiled"] <= rung["total"]
    r4 = document["r4"]
    assert r4["trees"] > 0
    lanes = {lane["lane"]: lane for lane in r4["lanes"]}
    assert set(lanes) == {"native", "pack", "extern", "island", "absent"}
    share = sum(lane["share"] for lane in lanes.values())
    assert abs(share - 1.0) < 0.001
