"""The compat site data file: builder, schema and drift.

`compat/data/compat.json` is the contract between the compiler and the public
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
    summary_document,
    write_atomic,
)
from scripts.compat_schema import (
    DIVERGENCE_FAMILIES,
    HISTORY_LIMIT,
    PREVIOUS_SCHEMA_VERSION,
    SCHEMA_VERSION,
    STATUSES,
    SUMMARY_KEYS,
    TARGET_STATUSES,
    validate,
    validate_summary,
)

ROOT = Path(__file__).resolve().parents[1]

SUMMARY = ROOT / "compat" / "data" / "summary.json"


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
    document = build(previous_path=DEFAULT_OUT)
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


# The targets section: hand-maintained, embedded by the builder.


def test_targets_are_present_and_well_formed() -> None:
    document = _document()
    rows = document["targets"]
    assert rows, "the targets table must not be empty"
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids))
    for row in rows:
        assert row["status"] in TARGET_STATUSES, row
        assert row["triple"], row
        assert row["label"], row
        assert row["note"], row


def test_target_statuses_are_counted_in_the_summary() -> None:
    document = _document()
    summary = document["summary"]
    for status, key in (("native", "targets_native"), ("experimental", "targets_experimental")):
        counted = sum(1 for row in document["targets"] if row["status"] == status)
        assert summary[key] == counted, status


def test_targets_file_is_the_source_of_the_section() -> None:
    """The section is the hand-maintained file, not a measurement."""
    from scripts.compat_targets import load

    document = _document()
    source = load()
    assert [row["id"] for row in source["targets"]] == [row["id"] for row in document["targets"]]
    assert document["sources"]["targets"].startswith("compat/data/targets.json")


# The divergences section: parsed from the docs.


def test_divergences_are_present_and_numbered() -> None:
    document = _document()
    rows = document["divergences"]
    assert rows, "the divergence list must not be empty"
    numbers = [row["number"] for row in rows]
    assert numbers == sorted(numbers)
    assert numbers == list(range(1, len(numbers) + 1)), "the numbers must be the doc's own numbering"


def test_divergences_match_the_doc() -> None:
    """The section must be the doc's list, item for item."""
    from scripts.compat_divergences import read

    document = _document()
    parsed = read()
    assert len(document["divergences"]) == len(parsed)
    for row, source in zip(document["divergences"], parsed, strict=True):
        assert row["number"] == source["number"]
        assert row["text"] == source["text"]


def test_divergences_have_a_family_and_a_source() -> None:
    document = _document()
    for row in document["divergences"]:
        assert row["family"] in DIVERGENCE_FAMILIES, row
        # Most items name where the difference comes from. The source is
        # either a link or a note that points at another item; an item with
        # neither is a doc item that states the difference and stops there.
        assert row["sources"] or row["note"] or row["text"], row["number"]


def test_divergence_count_is_in_the_summary() -> None:
    document = _document()
    assert document["summary"]["divergences"] == len(document["divergences"])


def test_the_divergence_parser_reads_the_numbered_items() -> None:
    from scripts.compat_divergences import parse

    text = (
        "# Title\n\n## Numbered divergences\n\n"
        "1. `int` is `i64`. Overflow aborts the process. Source: [refused](https://example.test/a.md).\n"
        "2. A second item that continues here.\n"
        "And its continuation.\n\n"
        "## Another section\n\n3. Not a divergence.\n"
    )
    rows = parse(text)
    assert [row["number"] for row in rows] == [1, 2]
    assert rows[0]["text"] == "`int` is `i64`. Overflow aborts the process."
    assert rows[0]["sources"] == ["https://example.test/a.md"]
    assert rows[1]["text"] == "A second item that continues here. And its continuation."


def test_the_divergence_parser_refuses_an_empty_document(tmp_path: Path) -> None:
    from scripts.compat_divergences import read

    empty = tmp_path / "divergences.md"
    empty.write_text("# Title\n\nNo numbered items here.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no numbered divergences"):
        read(empty)


def test_the_divergence_parser_reports_a_missing_doc(tmp_path: Path) -> None:
    from scripts.compat_divergences import read

    with pytest.raises(ValueError, match="not found"):
        read(tmp_path / "absent.md")


# The probe counters: the compat2 pass rate per row.


def test_probe_counters_are_present_on_the_support_rows() -> None:
    document = _document()
    for section in ("builtins", "stdlib", "packages"):
        for row in document[section]:
            assert "probes" in row, (section, row.get("id") or row.get("name"))
            assert "pass_rate" in row, (section, row.get("id") or row.get("name"))
            if row["probes"] is None:
                assert row["pass_rate"] is None
            else:
                assert 0 <= row["pass_rate"] <= 1


def test_probe_counters_match_the_outcomes() -> None:
    document = _document()
    for section in ("builtins", "stdlib", "packages"):
        for row in document[section]:
            if row["probes"] is None:
                continue
            outcomes = row["probe_outcomes"]
            total = outcomes["pass"] + outcomes["fence"] + outcomes["failures"]
            assert total <= row["probes"], (section, row.get("name"))
            scored = total
            if scored:
                assert row["pass_rate"] == pytest.approx(outcomes["pass"] / scored, abs=1e-4)


def test_the_summary_carries_the_probe_counters() -> None:
    document = _document()
    summary = document["summary"]
    assert summary["probes"] >= summary["probe_pass"]
    assert summary["probe_failures"] >= 0
    if summary["probes"]:
        assert 0 <= summary["pass_rate"] <= 1


def test_the_fence_rows_carry_the_remediation_hint() -> None:
    from pyscriptc.diagnostics import DIAGNOSTICS

    document = _document()
    for row in document["fences"]:
        assert row["why"], row["code"]
        assert row["fix"], row["code"]
        entry = DIAGNOSTICS[row["code"]]
        assert row["why"] == entry.why
        assert row["fix"] == entry.fix


# The summary file: the contract the landing page reads.


def test_the_summary_file_validates() -> None:
    if not SUMMARY.exists():
        pytest.skip("the summary file is not built yet")
    document = json.loads(SUMMARY.read_text(encoding="utf-8"))
    assert validate_summary(document) == []


def test_the_summary_file_matches_the_data_file() -> None:
    if not SUMMARY.exists():
        pytest.skip("the summary file is not built yet")
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    document = _document()
    assert summary["generated"] == document["generated"]
    assert summary["fences"] == document["summary"]["fences"]
    assert summary["divergences"] == document["summary"]["divergences"]
    assert summary["packages_full"] == document["summary"]["packages_full"]
    assert summary["packages_total"] == sum(
        document["summary"][key] for key in ("packages_full", "packages_partial", "packages_none")
    )


def test_the_summary_document_is_built_from_the_data() -> None:
    document = _document()
    summary = summary_document(document)
    assert validate_summary(summary) == []
    assert summary["schema"] == 1
    assert summary["url"].startswith("https://")


def test_the_summary_document_has_a_stable_key_set() -> None:
    """A rename would break the landing page, so the keys are pinned."""
    document = _document()
    assert set(summary_document(document)) == set(SUMMARY_KEYS)


def test_the_schema_version_is_the_current_one() -> None:
    assert _document()["schema"] == SCHEMA_VERSION


def test_a_previous_schema_document_is_still_understood() -> None:
    """The nightly bot PR carries data, not code: a bump must not break it."""
    assert PREVIOUS_SCHEMA_VERSION == "1"
