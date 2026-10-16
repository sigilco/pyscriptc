"""Self-host fence census totals."""

from __future__ import annotations

from pathlib import Path

from benchmarks.compat2.census_self import build_self_census

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pyscriptc"


def test_self_census_ballpark() -> None:
    census = build_self_census(SRC)
    assert census["modules"] == 22
    # E1 item 2: the print family moves the fences from corpus/code shapes
    # into the lowerer; net +3 self-host fences from restlower print support.
    assert census["fences"] == 683
    assert 11_000 <= census["lines"] <= 12_400
    assert sum(p["fences"] for p in census["packages"]) == 683
