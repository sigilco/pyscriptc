"""Self-host fence census totals."""

from __future__ import annotations

from pathlib import Path

from benchmarks.compat2.census_self import build_self_census

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pyscriptc"


def test_self_census_ballpark() -> None:
    census = build_self_census(SRC)
    # doctor.py is the 28th module; log.py, telemetry.py, debug.py and rtcache.py each added a module.
    # T2-5 static folds: restlower/_folds.py adds a module (+15 fences of
    # fold-boundary docstrings and helpers, self-host only).
    # config.py (config surface: PEP 723, pyscriptc.toml/pyproject tables) adds 1 module.
    # coro.py (generator frames) adds one more.
    assert census["modules"] == 51
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
    # T2-3 parameter forms normalize into the named-parameter machinery (+1
    # _classes fence for the kwarg-dict binding path).
    # T2-5 static folds: restlower/_folds.py adds +15 fences (fold-boundary
    # docstrings and helpers, self-host only).
    # T2-2 ABC/Protocol erasure: +5 fences in _classes.py (the erased-base
    # and decorator spellings are self-host fenced branches).
    # config.py adds 37 self-host fences (tomllib parsing, dataclass, os/pathlib use).
    # coro.py (generator frame pass) adds 45 fences (dict-shaped IR annotations).
    assert census["fences"] == 1537
    # 22,164 lines after the `--facts` lane landed in typefacts.py.
    assert 11_000 <= census["lines"] <= 25_000
    assert sum(p["fences"] for p in census["packages"]) == 1537
