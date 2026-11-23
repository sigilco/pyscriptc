# SPDX-License-Identifier: Apache-2.0
"""The differential probe lane (#523).

The lane is the producer the divergence ledger consumes: it compiles a corpus
of small programs with the current compiler, runs each one next to the pinned
CPython 3.14 oracle, and byte-compares stdout, stderr and the exit code. Any
difference is a candidate divergence.

These tests pin the lane's contract, not the compiler's:

- it covers both inputs, the semantic probes and the visible corpus,
- it never reaches the scorer holdout,
- it compares the three channels on untrimmed bytes,
- a divergence probe is judged against the recorded ledger row, so a known
  divergence is not reported as a new finding and a moved one is,
- the report is machine-readable and names every program,
- the lane is report-only: it exits 0 on a difference.
"""

from __future__ import annotations

import json

from benchmarks.probe_lane import lane
from tests.test_gates import ROOT

HOLDOUT = ROOT / "benchmarks" / "compat2" / "corpus" / "holdout"


def test_the_lane_covers_the_semantic_probes() -> None:
    programs = lane.semantic_probes()
    names = {p.name for p in programs}
    assert programs
    assert any(name.startswith("differential/") for name in names)
    assert any(name.startswith("divergences/") for name in names)


def test_the_lane_covers_the_visible_corpus() -> None:
    programs = lane.corpus_programs()
    names = {p.name for p in programs}
    assert programs
    assert any(name.startswith("scripts/") for name in names)
    assert any(name.startswith("src/") for name in names)


def test_the_lane_never_reaches_the_holdout() -> None:
    """The holdout is the scorer's; the lane must not read it."""

    for program in [*lane.semantic_probes(), *lane.corpus_programs()]:
        assert "holdout" not in program.path.parts
        assert HOLDOUT not in program.path.parents
        assert not program.path.is_relative_to(HOLDOUT)


def test_the_lane_source_never_names_the_holdout_directory() -> None:
    """The lane module itself must not carry a code path into the holdout."""

    source = (ROOT / "benchmarks" / "probe_lane" / "lane.py").read_text(encoding="utf-8")
    assert "corpus/holdout" not in source
    assert "holdout" not in source.replace("holdout is the scorer's", "")


def test_the_corpus_programs_stay_inside_the_visible_corpus() -> None:
    corpus = ROOT / "benchmarks" / "compat2" / "corpus"
    for program in lane.corpus_programs():
        assert corpus in program.path.parents


def test_first_byte_diff_names_the_offset_and_the_two_bytes() -> None:
    note = lane.first_byte_diff(b"abc", b"abd")
    assert "byte 2" in note
    assert "0x63" in note
    assert "0x64" in note


def test_first_byte_diff_is_empty_for_equal_bytes() -> None:
    assert lane.first_byte_diff(b"same", b"same") == ""


def test_first_byte_diff_reports_a_length_difference() -> None:
    note = lane.first_byte_diff(b"ab", b"abc")
    assert "length 2 != 3" in note
    assert "cpython" in note


def test_channel_diffs_names_every_disagreeing_channel() -> None:
    native = lane.Observation(b"out", b"err", 0)
    cpython = lane.Observation(b"ou", b"err", 1)
    diffs = lane.channel_diffs(native, cpython)
    assert any("stdout" in d for d in diffs)
    assert any("exit 1!=0" in d for d in diffs)
    assert not any("stderr" in d for d in diffs)


def test_channel_diffs_is_empty_when_the_sides_agree() -> None:
    obs = lane.Observation(b"out", b"err", 0)
    assert lane.channel_diffs(obs, obs) == []


def test_the_comparison_is_on_untrimmed_bytes() -> None:
    """A trailing space is a difference the stripped scorer cannot see."""

    native = lane.Observation(b"value\n", b"", 0)
    cpython = lane.Observation(b"value \n", b"", 0)
    diffs = lane.channel_diffs(native, cpython)
    assert diffs
    assert "stdout" in diffs[0]


def test_the_verdict_tally_has_a_fixed_shape() -> None:
    tally = lane.counts([])
    assert set(tally) == {"MATCH", "KNOWN", "DIFF", "FENCED", "ERROR", "SKIP"}
    assert all(value == 0 for value in tally.values())


def test_the_counts_line_carries_the_four_numbers_the_issue_names() -> None:
    line = lane.counts_line([])
    assert "compiled" in line
    assert "match" in line
    assert "differ" in line


def test_the_summary_names_the_compiled_matched_and_differed_counts() -> None:
    rows = [
        lane.LaneRow(name="a", lane="probes", source="a.py", verdict=lane.MATCH, compile_status="compiled"),
        lane.LaneRow(
            name="b",
            lane="probes",
            source="b.py",
            verdict=lane.DIFF,
            compile_status="compiled",
            first_diff="stdout first differs at byte 0",
        ),
    ]
    summary = lane.render_summary(rows)
    assert "- programs compiled: 2" in summary
    assert "- matched: 1" in summary
    assert "- differed: 1" in summary
    assert "`b`" in summary
    assert "stdout first differs at byte 0" in summary


def test_the_summary_lists_every_differing_program() -> None:
    rows = [
        lane.LaneRow(
            name=f"p{i}",
            lane="probes",
            source=f"p{i}.py",
            verdict=lane.DIFF,
            compile_status="compiled",
            first_diff=f"diff {i}",
        )
        for i in range(3)
    ]
    summary = lane.render_summary(rows)
    for i in range(3):
        assert f"`p{i}`" in summary


def test_the_report_is_machine_readable_and_names_every_program() -> None:
    rows = [
        lane.LaneRow(name="a", lane="probes", source="a.py", verdict=lane.MATCH, compile_status="compiled"),
        lane.LaneRow(
            name="b",
            lane="corpus",
            source="b.py",
            verdict=lane.DIFF,
            compile_status="compiled",
            first_diff="stdout first differs at byte 3",
        ),
    ]
    report = lane.build_report(rows, "/usr/bin/python3.14", 1.5)
    assert json.loads(json.dumps(report))["summary"]["programs"] == 2
    assert report["summary"]["matched"] == 1
    assert report["summary"]["differed"] == 1
    assert [row["name"] for row in report["rows"]] == ["a", "b"]
    assert report["rows"][1]["first_diff"] == "stdout first differs at byte 3"


def test_a_ledger_probe_is_judged_against_its_recorded_row() -> None:
    """A known divergence is not a finding; a moved one is."""

    rows = lane.load_ledger()
    assert rows, "the divergence ledger carries rows"
    probed = [row for row in rows.values() if row.get("probe")]
    assert probed
    for row in probed:
        assert row["cpython"] is not None
        assert row["pyscriptc"] is not None


def test_the_ledger_lookup_finds_the_row_of_a_divergence_probe() -> None:
    found = lane.ledger_row_for(lane.PROBES / "divergences" / "d02_floordiv_zero.py")
    assert found is not None
    row_id, row = found
    assert row_id.startswith("D")
    assert row["probe"].endswith("d02_floordiv_zero.py")


def test_the_ledger_lookup_ignores_a_probe_outside_the_divergences_set() -> None:
    assert lane.ledger_row_for(lane.PROBES / "differential" / "os_system.py") is None


def test_the_lane_exits_zero_on_a_difference() -> None:
    """The lane reports; it never blocks a pull request."""

    assert lane.main(["--lane", "probes", "--only", "no-such-program-anywhere"]) == 2
