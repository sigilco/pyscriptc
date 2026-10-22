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
    # stubs.py + sigregistry.py (stub reader, signature registry, not lowered): +2 modules.
    # imports.py (per-import lane classifier for the adoption ladder) adds one more.
    # deps.py (PyPI resolver, wheel cache) adds 1 module and about 125 fences, with its cli.py hook.
    # Script stdlib families (#188): +3 modules (restlower _ospath, _subprocess, _itertools) and +127 self-host fences.
    # restlower/_threading.py (native threading locks, #190) adds 1 module and 13 fences.
    assert census["modules"] == 60
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
    # stubs.py + sigregistry.py (stub reader, signature registry, not lowered): +2 modules, +77 fences.
    # imports.py adds 1 module and 48 fences; the `pyscriptc imports` subcommand registration adds 16 fences in cli.py.
    # Program directories (R2): the import graph walk, init-owner map, cycle fence, `__main__` guard branch,
    # entry inference and `--program` plumbing add 16 self-host fences (package.py, lower/, cli.py).
    # runcache.py (binary cache for `pyscriptc run`) adds 1 module; the `run` subcommand adds fences in cli.py.
    # PEP 723 dependency lanes, stub-path and ffi keys add 17 self-host fences (config.py, cli.py).
    # E2 M2-M5 (exception hierarchy, object surface, ZeroDivisionError variants): +23 fences (log, telemetry, llvm).
    assert census["fences"] == 2027
    # 22,164 lines after the `--facts` lane landed in typefacts.py.
    assert 11_000 <= census["lines"] <= 30_000
    assert sum(p["fences"] for p in census["packages"]) == 2027
