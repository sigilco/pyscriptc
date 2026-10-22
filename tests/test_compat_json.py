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
