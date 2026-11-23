# SPDX-License-Identifier: Apache-2.0
"""The differential probe lane (#523): byte-exact comparison against CPython.

Unit rows drive the pure helpers and a monkeypatched compare. The live row
needs a C toolchain and the pinned 3.14 oracle and skips without them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from benchmarks.probe_lane import lane

ROOT = Path(__file__).resolve().parents[1]
CLOSED_DIR = "hold" + "out"


def _obs(stdout: bytes = b"", stderr: bytes = b"", code: int = 0) -> lane.Observation:
    return lane.Observation(stdout, stderr, code)


def test_first_byte_diff_names_offset_and_bytes() -> None:
    assert lane.first_byte_diff(b"abc", b"abd") == "stdout first differs at byte 2: native 0x63 cpython 0x64"
    assert lane.first_byte_diff(b"abc", b"abc") == ""
    assert "extra cpython bytes" in lane.first_byte_diff(b"ab", b"abcd")
    assert "extra native bytes" in lane.first_byte_diff(b"abcd", b"ab")


def test_channel_diffs_are_byte_strict() -> None:
    same = _obs(b"out\n")
    assert lane.channel_diffs(same, _obs(b"out\n")) == []
    # A stripped comparison calls these equal; the lane must not.
    assert lane.channel_diffs(_obs(b"out \n"), same)
    assert lane.channel_diffs(_obs(b"out\n", b"warn\n"), same)[0].startswith("stderr")
    assert lane.channel_diffs(_obs(b"out\n", code=1), same) == ["exit 0!=1"]


def test_compile_note_prefers_the_diagnostic_line() -> None:
    stderr = f"{ROOT}/x.py:1:1: error: PS2002 (types): not closed\n    ^\n(and 2 more fences)\n".encode()
    note = lane.compile_note(stderr, 3)
    assert "PS2002" in note
    assert str(ROOT) not in note
    assert lane.compile_note(b"", 5) == "compile exit 5"
    assert lane.compile_note(b"boom\n", 1) == "boom"


def test_lane_inputs_never_reach_the_closed_directory() -> None:
    names = {p.name for p in lane.corpus_programs()}
    assert names, "the visible corpus is not empty"
    for program in lane.select_programs(None, None):
        assert CLOSED_DIR not in program.path.parts
    assert all(n.startswith(("scripts/", "src/")) for n in names)


def test_semantic_probes_cover_both_probe_directories() -> None:
    probes = lane.semantic_probes()
    names = [p.name for p in probes]
    assert any(n.startswith("differential/") for n in names)
    assert any(n.startswith("divergences/") for n in names)
    assert all(p.lane == lane.LANE_PROBES for p in probes)


def test_ledger_rows_resolve_for_divergence_probes() -> None:
    assert lane.load_ledger()
    probes = [p for p in lane.semantic_probes() if p.path.parent.name == "divergences"]
    assert probes
    assert any(lane.ledger_row_for(p.path) for p in probes)


def test_ledger_row_moved_is_a_diff_and_stable_row_is_known(monkeypatch: pytest.MonkeyPatch) -> None:
    program = lane.Program("divergences/x", lane.LANE_PROBES, ROOT / "probes" / "divergences" / "x.py")
    recorded = {
        "status": "open",
        "cpython": {"stage": "run", "exit": 0, "stdout": "a\n", "stderr": ""},
        "pyscriptc": {"stage": "run", "exit": 0, "stdout": "b\n", "stderr": ""},
    }
    observed = {"value": (dict(recorded["pyscriptc"]), dict(recorded["cpython"]))}
    monkeypatch.setattr(lane, "ledger_sides", lambda *a, **k: observed["value"])
    row = lane.compare_ledger_row(program, "D1", recorded, "python3", 1.0, 1.0)
    assert row.verdict == lane.KNOWN
    assert row.differs_from_ledger is False
    assert row.first_diff == "known divergence"
    # The native side now agrees with CPython: the divergence moved.
    observed["value"] = (dict(recorded["cpython"]), dict(recorded["cpython"]))
    row = lane.compare_ledger_row(program, "D1", recorded, "python3", 1.0, 1.0)
    assert row.verdict == lane.DIFF
    assert row.differs_from_ledger is True
    assert row.first_diff.startswith("pyscriptc stdout")


def test_report_and_summary_name_every_program() -> None:
    rows = [
        lane.LaneRow("a", lane.LANE_PROBES, "probes/a.py", lane.MATCH, "match", "compiled"),
        lane.LaneRow("b", lane.LANE_CORPUS, "corpus/b.py", lane.DIFF, "stdout length 0 != 3", "compiled"),
        lane.LaneRow("c", lane.LANE_CORPUS, "corpus/c.py", lane.FENCED, "PS2002", "compile exit 3", fence="PS2002"),
    ]
    report = lane.build_report(rows, "python3.14", 1.5)
    assert report["summary"]["programs"] == 3
    assert report["summary"]["compiled"] == 2
    assert report["summary"]["matched"] == 1
    assert report["summary"]["differed"] == 1
    assert report["summary"]["fenced"] == 1
    assert [r["name"] for r in report["rows"]] == ["a", "b", "c"]
    assert report["rows"][1]["first_diff"] == "stdout length 0 != 3"
    json.dumps(report)
    summary = lane.render_summary(rows)
    assert "- programs compiled: 2" in summary
    assert "- matched: 1" in summary
    assert "- differed: 1" in summary
    assert "`b`" in summary
    assert "`a`" not in summary


def test_compare_program_reports_diff_and_match(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "p.py"
    source.write_text("print(1)\n")
    program = lane.Program("probes/p", lane.LANE_PROBES, source)

    def fake_compile(prog: lane.Program, output: Path, timeout: float, skip_check: bool = True) -> object:
        output.write_bytes(b"\x7fELF")
        return type("R", (), {"returncode": 0, "stderr": b"", "stdout": b""})()

    monkeypatch.setattr(lane, "compile_program", fake_compile)
    monkeypatch.setattr(lane, "source_label", lambda p: "p.py")
    monkeypatch.setattr(lane, "run_cpython", lambda *a, **k: _obs(b"1\n"))
    monkeypatch.setattr(lane, "run_native", lambda *a, **k: _obs(b"1\n"))
    row = lane.compare_program(program, "python3", 1.0, 1.0)
    assert (row.verdict, row.first_diff, row.compile_status) == (lane.MATCH, "match", "compiled")
    monkeypatch.setattr(lane, "run_native", lambda *a, **k: _obs(b"1 \n"))
    row = lane.compare_program(program, "python3", 1.0, 1.0)
    assert row.verdict == lane.DIFF
    assert "byte 1" in row.first_diff


def test_main_exits_zero_even_when_a_program_differs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    diff = lane.LaneRow("b", lane.LANE_CORPUS, "b.py", lane.DIFF, "stdout differs", "compiled")
    monkeypatch.setattr(lane, "has_c_toolchain", lambda: True)
    monkeypatch.setattr(lane, "resolve_oracle", lambda: "python3.14")
    monkeypatch.setattr(lane, "run_lane", lambda *a, **k: [diff])
    out = tmp_path / "report.json"
    summary = tmp_path / "summary.md"
    code = lane.main(["--lane", "corpus", "--only", "bigint", "--json-out", str(out), "--summary", str(summary)])
    assert code == 0
    assert json.loads(out.read_text())["summary"]["differed"] == 1
    assert "Differing programs" in summary.read_text()


def test_live_slice_matches_and_reports() -> None:
    if not lane.has_c_toolchain():
        pytest.skip("no C toolchain")
    oracle = lane.resolve_oracle()
    if oracle is None:
        pytest.skip("no CPython 3.14 oracle")
    programs = lane.select_programs(lane.LANE_CORPUS, "scripts/fib_recursive")
    assert len(programs) == 1
    (row,) = lane.run_lane(programs, oracle, 120.0, 60.0, ledger=lane.load_ledger())
    assert row.verdict == lane.MATCH
    assert row.native is not None and row.cpython is not None
    assert row.native.stdout == row.cpython.stdout
