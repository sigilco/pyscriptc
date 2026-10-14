"""Set-cover ranking in benchmarks.compat2.census."""

from __future__ import annotations

from benchmarks.compat2.census import rank_families


def pkg(name: str, **families: int) -> dict:
    return {"package": name, "rootFamilies": families}


def test_single_family_package_completes_first() -> None:
    ranking = rank_families([pkg("a", union=3), pkg("b", union=1, kwonly=2), pkg("c", kwonly=1, decorators=1)])
    assert ranking[0]["family"] == "union"
    assert ranking[0]["newlyUnblocked"] == ["a"]
    assert [item["cumulativeUnblocked"] for item in ranking] == sorted(item["cumulativeUnblocked"] for item in ranking)
    assert ranking[-1]["cumulativeUnblocked"] == 3


def test_clean_packages_are_ignored() -> None:
    assert rank_families([pkg("a")]) == []
