"""The compat site data file: builder, schema and drift.

`data/compat/compat.json` is the contract between the compiler and the public
compat site. These tests keep it honest: the schema is enforced, the builder
is deterministic, and the committed file matches what the tree measures.

Design doc: .agents/docs/compat-site.md
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.build_compat_json import (
    DEFAULT_OUT,
    STATUS_FULL,
    STATUS_NONE,
    STATUS_PARTIAL,
    build,
    check,
    render,
    write_atomic,
)
from scripts.compat_schema import HISTORY_LIMIT, SCHEMA_VERSION, STATUSES, validate

ROOT = Path(__file__).resolve().parents[1]


def _document() -> dict:
    """The committed file, or a fresh build when it is not there yet."""
    if DEFAULT_OUT.exists():
        return json.loads(DEFAULT_OUT.read_text(encoding="utf-8"))
    return build()


def test_committed_file_validates() -> None:
    document = _document()
    assert validate(document) == []


def test_committed_file_has_the_schema_version() -> None:
    assert _document()["schema"] == SCHEMA_VERSION


def test_committed_file_covers_every_section() -> None:
    document = _document()
    for key in ("builtins", "stdlib", "packages", "language", "fences", "history", "summary", "sources"):
        assert key in document, key


def test_statuses_are_from_the_vocabulary() -> None:
    document = _document()
    for section in ("builtins", "stdlib", "packages"):
        for row in document[section]:
            assert row["status"] in STATUSES, row


def test_rows_have_unique_ids() -> None:
    document = _document()
    for section in ("builtins", "stdlib"):
        ids = [row["id"] for row in document[section]]
        assert len(ids) == len(set(ids)), section


def test_package_rows_carry_the_pinned_version() -> None:
    document = _document()
    pins = json.loads((ROOT / "benchmarks" / "compat" / "top100" / "pins.json").read_text(encoding="utf-8"))
    for row in document["packages"]:
        pin = pins["packages"].get(row["name"])
        assert pin is not None, row["name"]
        assert row["version"] == pin["version"], row["name"]


def test_package_status_follows_the_fence_ratio() -> None:
    document = _document()
    for row in document["packages"]:
        if row["fences"] == 0:
            assert row["status"] == STATUS_FULL, row["name"]
            continue
        half = row["fenced_definitions"] * 2 <= row["definitions"]
        assert row["status"] == (STATUS_PARTIAL if half else STATUS_NONE), row["name"]


def test_fence_counts_match_the_package_rows() -> None:
    document = _document()
    counted = sum(row["count"] for row in document["fences"])
    total = sum(row["fences"] for row in document["packages"])
    assert counted == total
    assert document["summary"]["fences"] == total


def test_fence_codes_are_known_diagnostics() -> None:
    from pyscriptc.diagnostics import DIAGNOSTICS

    document = _document()
    for row in document["fences"]:
        assert row["code"] in DIAGNOSTICS, row["code"]
        assert row["title"] == DIAGNOSTICS[row["code"]].title


def test_summary_counts_match_the_rows() -> None:
    document = _document()
    summary = document["summary"]
    for section, prefix in (("builtins", "builtins"), ("stdlib", "stdlib")):
        for status, key in ((STATUS_FULL, "full"), (STATUS_PARTIAL, "partial"), (STATUS_NONE, "none")):
            counted = sum(1 for row in document[section] if row["status"] == status)
            assert summary[f"{prefix}_{key}"] == counted, (section, status)
    for status, key in ((STATUS_FULL, "full"), (STATUS_PARTIAL, "partial"), (STATUS_NONE, "none")):
        counted = sum(1 for row in document["packages"] if row["status"] == status)
        assert summary[f"packages_{key}"] == counted, status


def test_history_is_sorted_and_capped() -> None:
    document = _document()
    dates = [row["date"] for row in document["history"]]
    assert dates == sorted(dates)
    assert len(dates) <= HISTORY_LIMIT
    assert len(dates) == len(set(dates))


def test_history_last_point_matches_the_summary() -> None:
    document = _document()
    if not document["history"]:
        pytest.skip("no history yet")
    last = document["history"][-1]
    assert last["date"] == document["generated"]
    assert last["packages_full"] == document["summary"]["packages_full"]
    assert last["packages_none"] == document["summary"]["packages_none"]


def test_render_is_stable() -> None:
    document = _document()
    assert render(document) == render(json.loads(render(document)))


def test_write_atomic_leaves_no_temp_file(tmp_path: Path) -> None:
    out = tmp_path / "compat.json"
    write_atomic(out, "{}\n")
    assert out.read_text(encoding="utf-8") == "{}\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["compat.json"]


def test_check_reports_a_missing_file(tmp_path: Path) -> None:
    document = _document()
    problems = check(document, tmp_path / "absent.json")
    assert problems and "missing" in problems[0]


def test_check_reports_a_stale_file(tmp_path: Path) -> None:
    document = _document()
    out = tmp_path / "compat.json"
    out.write_text(render({**document, "summary": {}}), encoding="utf-8")
    problems = check(document, out)
    assert problems and "stale" in problems[0]


def test_check_passes_on_the_committed_file() -> None:
    if not DEFAULT_OUT.exists():
        pytest.skip("the compat file is not built yet")
    # A file the nightly built carries the scorer output (language rows, weighted
    # pass). Rebuild over the same score so the check compares like with like;
    # when the file names a score that is not on disk, it cannot be reproduced.
    recorded = json.loads(DEFAULT_OUT.read_text(encoding="utf-8")).get("sources", {}).get("score", "")
    score_path = ROOT / recorded if recorded else None
    if score_path is not None and not score_path.exists():
        pytest.skip(f"the committed file was built over {recorded}, which is not on disk")
    document = build(previous_path=DEFAULT_OUT, score_path=score_path)
    assert check(document, DEFAULT_OUT) == []


def test_builder_is_deterministic() -> None:
    first = build(previous_path=None)
    second = build(previous_path=None)
    assert render(first) == render(second)


def test_builder_reads_the_score_output(tmp_path: Path) -> None:
    score = {
        "compiler_hash": "abc",
        "weights": {"lang.one": 1.0, "lang.two": 1.0},
        "rows": [
            {"id": "lang.one", "outcome": "pass", "code": ""},
            {"id": "lang.two", "outcome": "fence", "code": "PS2002"},
        ],
    }
    path = tmp_path / "score.json"
    path.write_text(json.dumps(score), encoding="utf-8")
    document = build(score_path=path, previous_path=None)
    assert document["language"]
    group = next(row for row in document["language"] if row["id"] == "lang")
    assert group["probes"] == 2
    assert group["outcomes"] == {"fence": 1, "pass": 1}
    assert group["weighted_pass"] == 1.0
    assert group["fence_codes"] == {"PS2002": 1}


def test_history_appends_one_point_per_date(tmp_path: Path) -> None:
    previous = tmp_path / "previous.json"
    previous.write_text(
        json.dumps(
            {
                "history": [
                    {
                        "date": "2026-10-20",
                        "commit": "aaa",
                        "weighted_pass": 1.0,
                        "packages_full": 1,
                        "packages_partial": 2,
                        "packages_none": 3,
                        "fences": 10,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    document = build(previous_path=previous, date="2026-10-21")
    dates = [row["date"] for row in document["history"]]
    assert dates == ["2026-10-20", "2026-10-21"]
    assert document["history"][-1]["commit"] == document["commit"]


def test_history_replaces_the_same_date(tmp_path: Path) -> None:
    previous = {
        "history": [
            {
                "date": "2026-10-21",
                "commit": "aaa",
                "weighted_pass": 1.0,
                "packages_full": 1,
                "packages_partial": 2,
                "packages_none": 3,
                "fences": 10,
            }
        ]
    }
    path = tmp_path / "previous.json"
    path.write_text(json.dumps(previous), encoding="utf-8")
    document = build(previous_path=path, date="2026-10-21")
    dates = [row["date"] for row in document["history"]]
    assert dates == ["2026-10-21"]


def test_validate_catches_a_bad_status() -> None:
    document = _document()
    document["packages"][0]["status"] = "green"
    problems = validate(document)
    assert any("status" in problem for problem in problems)


def test_validate_catches_a_missing_key() -> None:
    document = _document()
    del document["packages"][0]["version"]
    problems = validate(document)
    assert any("version" in problem for problem in problems)


def test_validate_catches_a_bad_outcome() -> None:
    document = _document()
    document["language"] = [
        {
            "id": "lang",
            "label": "language",
            "probes": 1,
            "outcomes": {"maybe": 1},
            "weighted_pass": 0.0,
        }
    ]
    problems = validate(document)
    assert any("maybe" in problem for problem in problems)


def test_validate_catches_an_unknown_top_level_key() -> None:
    document = _document()
    document["extra"] = 1
    problems = validate(document)
    assert any("extra" in problem for problem in problems)


def test_ladder_section_matches_the_committed_document() -> None:
    document = _document()
    ladder = document.get("ladder")
    assert ladder is not None
    rungs = {row["rung"] for row in ladder["rungs"]}
    assert rungs == {"R1", "R2"}
    lanes = {row["lane"] for row in ladder["lanes"]}
    assert lanes == {"native", "pack", "extern", "island", "absent"}
    share = sum(row["share"] for row in ladder["lanes"])
    assert abs(share - 1.0) < 0.001


def test_ladder_rung_pass_never_exceeds_the_total() -> None:
    document = _document()
    for row in document["ladder"]["rungs"]:
        assert 0 <= row["passed"] <= row["total"], row
        assert 0 <= row["compiled"] <= row["total"], row


def test_builder_reads_the_ladder_document(tmp_path: Path) -> None:
    ladder = {
        "schema": 1,
        "rungs": [
            {
                "rung": "R1",
                "label": "Single-file script",
                "corpus": "scripts",
                "total": 4,
                "compiled": 3,
                "passed": 2,
                "measured": True,
            }
        ],
        "r4": {
            "rung": "R4",
            "corpus": "top100",
            "total": 10,
            "trees": 2,
            "treesWithForeignLane": 1,
            "selfContainedShare": 0.5,
            "lanes": [
                {"lane": "native", "imports": 8, "share": 0.8},
                {"lane": "extern", "imports": 2, "share": 0.2},
            ],
        },
        "r0": {
            "rung": "R0",
            "label": "Vendored real script",
            "corpus": "benchmarks/real_scripts/scripts",
            "total": 39,
            "passed": 2,
            "rate": 0.0513,
            "fences": 294,
            "date": "2026-11-09",
            "blockers": [
                {"family": "module-assign", "rootCause": "statements", "scriptsBlocked": 2, "scriptsAlone": 1}
            ],
            "rootCauses": [{"cause": "inference", "scriptsReleased": 3}],
            "history": [{"date": "2026-11-08", "passed": 1, "total": 39, "rate": 0.0256}],
        },
    }
    path = tmp_path / "ladder.json"
    path.write_text(json.dumps(ladder), encoding="utf-8")
    document = build(previous_path=None, ladder_path=path)
    ladder_row = document["ladder"]
    assert ladder_row["corpus"] == "benchmarks/scripts_corpus"
    assert ladder_row["rungs"][0]["passed"] == 2
    assert ladder_row["selfContainedShare"] == 0.5
    assert [lane["lane"] for lane in ladder_row["lanes"]] == ["native", "extern"]
    assert ladder_row["r0"]["passed"] == 2
    assert ladder_row["r0"]["rate"] == 0.0513
    assert ladder_row["r0"]["total"] == 39
    assert ladder_row["r0"]["blockers"][0]["family"] == "module-assign"
    assert ladder_row["r0"]["rootCauses"][0]["cause"] == "inference"
    assert ladder_row["r0"]["history"] == [{"date": "2026-11-08", "passed": 1, "total": 39, "rate": 0.0256}]


def test_builder_without_a_ladder_document_renders_an_empty_section(tmp_path: Path) -> None:
    document = build(previous_path=None, ladder_path=tmp_path / "absent.json")
    assert document["ladder"] == {"corpus": "", "rungs": [], "lanes": [], "r0": {}}


def test_r0_lane_merges_the_point_and_the_trend(tmp_path: Path) -> None:
    from benchmarks.real_scripts.lane import HISTORY_LIMIT, merge_history
    from benchmarks.real_scripts.lane import build as r0_build

    measure = tmp_path / "r0.json"
    measure.write_text(
        json.dumps(
            {
                "generated": "2026-11-09",
                "scripts": [{"compilePass": True, "fences": 0}, {"compilePass": False, "fences": 3}],
            }
        ),
        encoding="utf-8",
    )
    section = r0_build(measure, {"r0": {"history": [{"date": "2026-11-08", "passed": 0, "total": 2, "rate": 0.0}]}})
    assert section["passed"] == 1
    assert section["total"] == 2
    assert section["rate"] == 0.5
    assert [row["date"] for row in section["history"]] == ["2026-11-08", "2026-11-09"]

    # A point whose date already sits in the trend replaces that point.
    replaced = merge_history(section["history"], {**section, "date": "2026-11-08"})
    assert len(replaced) == 2
    assert replaced[0]["date"] == "2026-11-08"
    assert replaced[0]["passed"] == section["passed"]

    # The window is capped.
    flood = merge_history(
        [{"date": f"2026-01-{day:02d}", "passed": 0, "total": 2, "rate": 0.0} for day in range(1, 41)],
        {**section, "date": "2026-02-01"},
    )
    assert len(flood) == HISTORY_LIMIT


def test_r0_rate_matches_the_committed_report() -> None:
    """The acceptance check: the published r0 rate equals the report for the same tree."""
    report = (ROOT / "benchmarks" / "real_scripts" / "REPORT.md").read_text(encoding="utf-8")
    ladder = json.loads((ROOT / "data" / "compat" / "ladder.json").read_text(encoding="utf-8"))
    r0 = ladder.get("r0", {})
    line = next(row for row in report.splitlines() if row.startswith("Rung R0:"))
    words = line.split()
    passed, total = int(words[2]), int(words[4])
    assert r0.get("passed") == passed
    assert r0.get("total") == total


def test_validate_catches_a_bad_ladder_lane() -> None:
    document = _document()
    document["ladder"]["lanes"][0]["lane"] = "warp"
    problems = validate(document)
    assert any("lane" in problem for problem in problems)


def test_validate_catches_a_bad_ladder_rung_count() -> None:
    document = _document()
    document["ladder"]["rungs"][0]["total"] = "forty-five"
    problems = validate(document)
    assert any("total" in problem for problem in problems)
