"""Self-host fence census totals."""

from __future__ import annotations

from pathlib import Path

from benchmarks.compat2.census_self import build_self_census

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pyscriptc"


def test_self_census_ballpark() -> None:
    census = build_self_census(SRC)
    assert census["modules"] == 18
    assert census["fences"] == 642
    assert 11_000 <= census["lines"] <= 12_000
    assert sum(p["fences"] for p in census["packages"]) == 642
