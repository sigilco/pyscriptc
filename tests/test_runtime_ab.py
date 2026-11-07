"""Runtime A/B harness: direct emitter vs opt-in MIR lane (epic #817, task B9).

The emit-flip release gate. The default program set is the in-slice MIR leaf
fixtures plus the 20 visible compat2 corpus scripts. Compiles each program
through both lanes, runs the binaries in isolated temp dirs, and requires
stdout, stderr and the return code to match. Skips cleanly when no C
toolchain is on PATH. Does not
change either lane.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.runtime_ab import (
    CORPUS_SCRIPTS,
    LEAF_DIR,
    PASS,
    SKIP,
    Observation,
    ProgramVerdict,
    compare_program,
    compare_programs,
    corpus_script_paths,
    default_programs,
    has_c_toolchain,
    lane_env,
    leaf_fixture_paths,
    main,
    render_summary,
    render_table,
    slice_counts,
)

from pyscriptc.miremit import MIR_ENV

ROOT = Path(__file__).resolve().parents[1]


def test_leaf_fixtures_exist() -> None:
    paths = leaf_fixture_paths()
    assert paths, f"no MIR leaf fixtures under {LEAF_DIR}"
    names = {path.name for path in paths}
    assert "leafint.py" in names
    assert "mirmix.py" in names


def test_default_programs_cover_leaf_fixtures_and_corpus_scripts() -> None:
    defaults = default_programs()
    assert defaults
    assert defaults == sorted(defaults, key=lambda p: str(p))
    leaf_defaults = {path for path in defaults if path.parent == LEAF_DIR}
    corpus_defaults = {path for path in defaults if path.parent.parent == CORPUS_SCRIPTS}
    # Every in-slice leaf fixture is in; corpus scripts are all in, in-slice or not.
    expected_leaf = {path for path in leaf_fixture_paths() if slice_counts(path)[0] > 0}
    assert leaf_defaults == expected_leaf
    assert corpus_defaults == set(corpus_script_paths())
    for path in leaf_defaults:
        in_slice, total = slice_counts(path)
        assert in_slice > 0
        assert total >= in_slice


def test_corpus_script_paths_are_visible_scripts_only() -> None:
    paths = corpus_script_paths()
    assert len(paths) == 20
    for path in paths:
        assert path.name == "main.py"
        assert path.parent.parent == CORPUS_SCRIPTS
        # The visible scripts split only: no holdout or other split is reachable.
        assert "holdout" not in path.parts


def test_script_without_slice_skips(tmp_path: Path) -> None:
    """A script with no in-slice function reports SKIP, never crashes."""
    path = tmp_path / "noslice.py"
    path.write_text("async def g():\n    return 1\n")
    assert slice_counts(path)[0] == 0
    row = compare_program(path)
    assert row.verdict == SKIP
    assert row.in_slice == 0
    assert "supported_fn" in row.note


def test_mirmix_is_mixed_slice() -> None:
    """mirmix.py is the strangler sample: `g` is in-slice, `main` rides the direct path."""
    path = LEAF_DIR / "mirmix.py"
    in_slice, total = slice_counts(path)
    assert in_slice >= 1
    assert total > in_slice


def test_lane_env_isolates_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MIR_ENV, "1")
    direct = lane_env(False)
    mir = lane_env(True)
    assert direct[MIR_ENV] == "0"
    assert mir[MIR_ENV] == "1"
    assert "PYTHONHASHSEED" in direct and direct["PYTHONHASHSEED"] == "0"


def test_render_table_has_per_program_verdict() -> None:
    rows = [
        ProgramVerdict(
            LEAF_DIR / "leafint.py",
            PASS,
            3,
            4,
            "match",
            Observation(b"7\n", b"", 0),
            Observation(b"7\n", b"", 0),
        ),
        ProgramVerdict(LEAF_DIR / "skip.py", SKIP, 0, 1, "no function where supported_fn holds"),
    ]
    table = render_table(rows)
    assert "program" in table
    assert "verdict" in table
    assert "leafint.py" in table
    assert PASS in table
    assert SKIP in table
    assert "2 programs:" in table


def test_render_summary_has_per_program_verdict() -> None:
    rows = [
        ProgramVerdict(
            LEAF_DIR / "leafint.py",
            PASS,
            3,
            4,
            "match",
            Observation(b"7\n", b"", 0),
            Observation(b"7\n", b"", 0),
        ),
        ProgramVerdict(LEAF_DIR / "skip.py", SKIP, 0, 1, "no function where supported_fn holds"),
    ]
    text = render_summary(rows)
    assert "### Runtime A/B" in text
    assert "| program |" in text
    assert "leafint.py" in text
    assert PASS in text
    assert SKIP in text
    assert "Report only" in text


def test_json_out_and_summary_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.runtime_ab.has_c_toolchain", lambda: True)
    monkeypatch.setattr(
        "scripts.runtime_ab.compare_programs",
        lambda programs, timeout, run_timeout: [
            ProgramVerdict(LEAF_DIR / "leafint.py", PASS, 1, 1, "match"),
        ],
    )
    json_out = tmp_path / "ab.json"
    summary = tmp_path / "summary.md"
    summary.write_text("prefix\n", encoding="utf-8")
    code = main(["--json-out", str(json_out), "--summary", str(summary)])
    assert code == 0
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["rows"][0]["verdict"] == PASS
    text = summary.read_text(encoding="utf-8")
    assert text.startswith("prefix\n")
    assert "leafint.py" in text
    assert "### Runtime A/B" in text


def test_nightly_wires_runtime_ab_as_report_only_lane() -> None:
    workflow = (ROOT / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")
    assert "scripts/runtime_ab.py" in workflow
    assert "--json-out nightly/runtime-ab.json" in workflow
    assert '--summary "$GITHUB_STEP_SUMMARY"' in workflow
    assert "runtime-ab:" in workflow
    assert "continue-on-error: true" in workflow
    # Report only: the failure-issue job does not wait on this lane.
    report = workflow.split("report-failure:")[-1]
    assert "needs: [visible-score, boxed-ledger, embed-acceptance]" in report
    assert "runtime-ab" not in report.split("if:")[0]


def test_unsupported_program_skips(tmp_path: Path) -> None:
    source = tmp_path / "str_list.py"
    source.write_text(
        "def total(xs: list[dict[str, int]]) -> int:\n    return len(xs)\n",
        encoding="utf-8",
    )
    row = compare_program(source)
    assert row.verdict == SKIP
    assert row.in_slice == 0
    assert "supported_fn" in row.note


def test_harness_skips_without_c_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.runtime_ab.has_c_toolchain", lambda: False)
    code = main([])
    assert code == 2


@pytest.mark.skipif(not has_c_toolchain(), reason="no C toolchain")
def test_leaf_fixtures_runtime_match() -> None:
    programs = [path for path in leaf_fixture_paths() if slice_counts(path)[0] > 0]
    rows = compare_programs(programs)
    assert rows, "default corpus was empty"
    failed = [row for row in rows if row.verdict != PASS]
    assert not failed, render_table(rows)
    names = {row.name for row in rows}
    assert "leafint.py" in names
    assert "mirmix.py" in names


def test_render_table_disambiguates_same_named_programs() -> None:
    corpus = corpus_script_paths()
    rows = [
        ProgramVerdict(corpus[0], SKIP, 0, 1, "no function where supported_fn holds"),
        ProgramVerdict(corpus[1], SKIP, 0, 1, "no function where supported_fn holds"),
    ]
    table = render_table(rows)
    # Both are `main.py`; the table qualifies them with the script directory.
    assert f"{corpus[0].parent.name}/main.py" in table
    assert f"{corpus[1].parent.name}/main.py" in table


@pytest.mark.skipif(not has_c_toolchain(), reason="no C toolchain")
def test_tool_prints_verdict_table() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "runtime_ab.py"), str(LEAF_DIR / "leafint.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert proc.returncode == 0, proc.stderr
    assert "verdict" in proc.stdout
    assert "leafint.py" in proc.stdout
    assert PASS in proc.stdout
    assert "1 programs:" in proc.stdout
