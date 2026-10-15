"""Full-set calibration shards merge deterministically, and triage reads per-id outcomes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from benchmarks.compat2.probes.calibrate import (
    MISSING_SWITCH,
    assign_shard,
    main,
    merge_oracle,
    merge_outcomes,
    merge_shard_reports,
    report_from_outcomes,
    resolve_oracle,
)
from benchmarks.compat2.probes.triage import build_report, problems

PROBES = Path(__file__).resolve().parents[1] / "benchmarks" / "compat2" / "probes"


def test_shards_partition_the_sorted_ids() -> None:
    rows = [{"id": f"p{i:02d}", "area": "a", "category": "b", "kind": "value"} for i in range(20)]
    parts = [assign_shard(rows, (index, 4)) for index in range(4)]
    ids = [str(row["id"]) for part in parts for row in part]
    assert sorted(ids) == [f"p{i:02d}" for i in range(20)]
    assert len(ids) == len(set(ids)) == 20
    assert [str(row["id"]) for row in assign_shard(rows, None)] == [f"p{i:02d}" for i in range(20)]


def test_merge_outcomes_is_deterministic() -> None:
    left = [
        {"id": "b", "outcome": "fence", "cell": "builtins/function/value"},
        {"id": "a", "outcome": "pass", "cell": "surface/constant/value"},
    ]
    right = [
        {"id": "c", "outcome": "build-error", "cell": "surface/function/value"},
        {"id": "a", "outcome": "pass", "cell": "surface/constant/value"},
    ]
    merged = merge_outcomes([left, right])
    assert merged == merge_outcomes([right, left])
    assert merged == merge_outcomes([list(reversed(right)), list(reversed(left))])
    assert [row["id"] for row in merged] == ["a", "b", "c"]
    oracle = {"version": "3.14.8", "executable": "/usr/bin/python3.14", "requested": None}
    one = report_from_outcomes(merged, set(), oracle)
    two = report_from_outcomes(merge_outcomes([right, left]), set(), oracle)
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)
    assert one["probes"] == 3
    assert one["totals"] == {"build-error": 1, "fence": 1, "pass": 1}


def test_merge_outcomes_rejects_conflicts() -> None:
    with pytest.raises(SystemExit, match="shard conflict"):
        merge_outcomes(
            [
                [{"id": "a", "outcome": "pass", "cell": "a/b/value"}],
                [{"id": "a", "outcome": "fence", "cell": "a/b/value"}],
            ]
        )


def test_merge_shard_files_ignore_input_order(tmp_path: Path) -> None:
    oracle = {"executable": sys.executable, "requested": None, "version": "3.14.8"}
    first = tmp_path / "shard-0.json"
    second = tmp_path / "shard-1.json"
    first.write_text(
        json.dumps(
            {
                "oracle": oracle,
                "shard": "0/2",
                "outcomes": [{"cell": "surface/constant/mention", "id": "b", "outcome": "mention"}],
            }
        ),
        encoding="utf-8",
    )
    second.write_text(
        json.dumps(
            {
                "oracle": {"requested": None, "version": "3.14.8", "executable": sys.executable},
                "shard": "1/2",
                "outcomes": [{"outcome": "pass", "cell": "builtins/function/value", "id": "a"}],
            }
        ),
        encoding="utf-8",
    )
    forward = merge_shard_reports([first, second], expect_ids=None)
    backward = merge_shard_reports([second, first], expect_ids=None)
    assert json.dumps(forward, sort_keys=True) == json.dumps(backward, sort_keys=True)
    assert [row["id"] for row in forward["outcomes"]] == ["a", "b"]  # type: ignore[index]
    assert forward["shards"] == ["0/2", "1/2"]
    assert merge_oracle([oracle, oracle]) == oracle


def test_oracle_rejects_unavailable_version() -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--oracle", "3.12"])
    message = str(caught.value)
    assert caught.value.code != 0
    assert "3.12" in message
    assert MISSING_SWITCH in message
    assert "PYSC_ORACLE_DIR" in message
    assert "generate.interpreter" in message


def test_oracle_accepts_the_running_interpreter() -> None:
    minor = f"{sys.version_info[0]}.{sys.version_info[1]}"
    current = resolve_oracle(None)
    assert current["executable"] == sys.executable
    assert current["requested"] is None
    assert str(current["version"]).startswith(minor + ".")
    requested = resolve_oracle(minor)
    assert requested["version"] == current["version"]
    assert requested["requested"] == minor
    assert resolve_oracle(str(current["version"]))["version"] == current["version"]


def test_triage_has_no_unclassified_and_matches_fence_count() -> None:
    report = build_report()
    assert problems(report) == []
    assert report.unclassified == ()
    assert len(report.fences) == report.fence_expected
    assert report.per_id_fences == report.fence_expected
    text = (PROBES / "TRIAGE.md").read_text(encoding="utf-8")
    assert f"| fence total | {len(report.fences)} |" in text
    assert "| unclassified | 0 |" in text
    assert "does not name its id" not in text
