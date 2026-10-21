"""Self-host fence census totals."""

from __future__ import annotations

from pathlib import Path

from benchmarks.compat2.census_self import build_self_census

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pyscriptc"


def test_self_census_ballpark() -> None:
    census = build_self_census(SRC)
    # doctor.py is the 28th module; log.py, telemetry.py, debug.py and rtcache.py each added a module.
    assert census["modules"] == 48
    # E1 items 2+7a: print support added 3 self-host fences, then the
    # list.append flip removed 20 fenced call sites; headroom 14k for wave-C lowers.
    # telemetry.py added 36 fences, log.py 20, debug.py 6 union-type
    # fences (`X | None` parameters); R1–R5 batch un-fenced 10 self-host
    # call sites (list/dict/str methods).
    # PyrightFactsAdapter (typefacts.py) added 139 fences: subprocess/threads/
    # JSON-RPC stdio plumbing has no scriptc-subset equivalent; the `--facts`
    # CLI lane adds 21 more (same plumbing family).
    # Pyrefly/TSP adapter plumbing + deprecation markers added more.
    # Checker resolver (sysconfig/which/env probing) in check.py adds 12 more.
    # Telemetry v1 events + CLI --fail-on/--print/passthrough add 14 more.
    # doctor.py, env defaults and --emit asm/obj add 1 module and 34 fences.
    # PyreflyTspFactsAdapter (typefacts.py) adds 23 more.
    # v0.3.1: mypy/pyright/pyright-typeserver adapters removed from typefacts.py (-1343 to 1242).
    # v0.4.0: restlower.py split into a package of handler modules (+19 modules, +140 fences).
    # Telemetry privacy pass: random install id, IP-discard properties, opt-in switch (+12 fences).
    # rtcache.py (runtime object cache: threads, hashing, subprocess) adds 1 module and 33 fences.
    # First-run fix: node_text/op_text diagnostics helpers and CLI --run entry handling (+7 fences).
    # Optional widening (Stage U): total stays 1434; 2 typefacts `X | Y | None` fences
    # move from union-types (11 to 9) into optional-types (0 to 2, PS1102).
    # Program directories (R2): the import graph walk, the init-owner map, the
    # cycle fence and the `__main__` guard branch add 11 self-host fences in
    # package.py and lower/; the `--program` mode (`python -m pkg` parity)
    # adds 3 more in package.py, cli.py and lower/.
    assert census["fences"] == 1450
    # 22,164 lines after the `--facts` lane landed in typefacts.py.
    assert 11_000 <= census["lines"] <= 25_000
    assert sum(p["fences"] for p in census["packages"]) == 1450
