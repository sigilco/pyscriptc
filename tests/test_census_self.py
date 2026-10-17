"""Self-host fence census totals."""

from __future__ import annotations

from pathlib import Path

from benchmarks.compat2.census_self import build_self_census

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pyscriptc"


def test_self_census_ballpark() -> None:
    census = build_self_census(SRC)
    assert census["modules"] == 23
    # E1 items 2+7a: print support added 3 self-host fences, then the
    # list.append flip removed 20 fenced call sites; headroom 13k for wave-C lowers.
    # telemetry.py (stdlib-only, runtime plumbing) added 36 fences.
    assert census["fences"] == 699
    assert 11_000 <= census["lines"] <= 13_000
    assert sum(p["fences"] for p in census["packages"]) == 699
