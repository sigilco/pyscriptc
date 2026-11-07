# MIR flip receipts (2026-11-07)

Decision #937 signed by the owner 2026-11-07 (merged b2f28bfe). Receipts on origin/master at the time of the run.

| # | Criterion | Result |
|---|---|---|
| 1 | Slice coverage (`scripts/mir_coverage.py`) | 42 of 42 corpus functions in slice, 100%; bar 80% |
| 2 | Runtime A/B (`scripts/runtime_ab.py`) | 68 programs: 64 PASS, 0 FAIL, 0 ERROR, 4 SKIP (binary_trees PS1003, exceptions_flow PS2003, generators_comp PS2003, json_like_dump PS1104 and PS2002: module fences, compile fails on both lanes) |
| 3 | Scorer outcome flips (`scripts/compat_mir_flip.py`, visible corpus, both lanes) | A to B: 0, B to A: 0 |
| 4 | Perf ratio (`scripts/mir_perf_ratio.py`) | MIR/direct geomean 1.021 (kernel 1.031, stdlib 1.023, scripting 0.933); per-workload rows inconclusive (noise) |
| 5 | Escape hatch | `tests/test_mir_escape_hatch.py::test_default_is_on`; `PYSC_MIR=0` forces the direct emitter |

## compat_mir_flip output

### Compat outcome flips

Compat (scorer v2, 13798 probes, oracle 2/cpython-3.14.9): pass 1649 and weighted pass 945.0 of 9786.7 (9.66%) on both files, with 0 outcome flips in either direction.

A to B: 0. B to A: 0.

| id | A | B |
|---|---|---|
| (none) | - | - |
