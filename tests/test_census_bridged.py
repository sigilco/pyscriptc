"""The bridged fence census (issue #258, item 5).

The census compares a plain compile against a bridged compile per package
and reports which fences retired. The census is evidence for the
adoption-ladder default-on question, not a gate.
"""

from __future__ import annotations

from pathlib import Path

from benchmarks.compat2.census_bridged import delta

PLAIN = {
    "package": "p",
    "fences": 4,
    "families": {"local-inference": 2, "return-inference": 1, "union-types": 1},
}
BRIDGED = {
    "package": "p",
    "fences": 2,
    "rootFences": 2,
    "families": {"local-inference": 1, "union-types": 1},
}


def test_delta_counts_retired_and_stayed() -> None:
    entry = delta(PLAIN, BRIDGED)
    assert entry["fences"] == {"plain": 4, "bridged": 2}
    assert entry["retired"] == {"local-inference": 1, "return-inference": 1}
    assert entry["grew"] == {}
    assert entry["net"] == -2


def test_delta_flags_growths() -> None:
    grown = {"package": "p", "fences": 5, "families": {"local-inference": 3, "union-types": 2}}
    entry = delta(PLAIN, grown)
    assert entry["grew"] == {"local-inference": 1, "union-types": 1}
    assert entry["net"] == 1


def test_build_bridged_census_reports_inference_families(tmp_path: Path, monkeypatch) -> None:
    from benchmarks.compat2 import census_bridged

    reports = {
        "a": {
            "summary": {"fences": 2},
            "fences": [
                {"family": "local-inference"},
                {"family": "local-inference"},
            ],
        },
        "b": {
            "summary": {"fences": 1},
            "fences": [{"family": "local-inference"}],
        },
    }

    def fake_build_report(target: Path, bridge=None):  # noqa: ANN001, ANN202
        return reports[target.name]

    bridged_reports = {
        "a": {
            "package": "a",
            "fences": 1,
            "rootFences": 1,
            "families": {"local-inference": 1},
        },
        "b": {
            "package": "b",
            "fences": 0,
            "rootFences": 0,
            "families": {},
        },
    }

    calls = {"n": 0}

    def fake_bridged(target: Path, provider: str):
        report = bridged_reports[target.name]
        calls["n"] += 1
        return report

    monkeypatch.setattr(census_bridged, "build_report", fake_build_report)
    monkeypatch.setattr(census_bridged, "bridged_package_census", fake_bridged)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    census = census_bridged.build_bridged_census(tmp_path, "mypy")
    assert calls["n"] == 2
    family = census["inferenceFamilies"]["local-inference"]
    assert family == {"plain": 3, "bridged": 1, "retired": 2}
    assert census["grew"] == []
    assert census["shifted"] == []
