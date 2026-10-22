# IR_HASHES.json regenerated on Linux (2026-10-22)

Decision: `benchmarks/compat2/probes/IR_HASHES.json` is generated only on Linux (ubuntu-24.04, CPython 3.14.x) by `.github/workflows/ir-hashes.yml`, and pull requests touching `src/pyscriptc/**`, the probes, `canonical.py` or `irscore.py` compare against it. Not a required check. Issue #211.

Why the old baseline was unusable: it was last regenerated at 0b9639a (#48) on macOS, and canonical IR folds host constants (socket, errno, os, ssl, time, sys, ctypes, pathlib). On macOS even the baseline's own commit differs by 458 hashes.

| Cause | Hashes moved |
| --- | --- |
| Host constants at the baseline commit (macOS vs Linux) | 458 |
| ce02cd5 | 666 |
| #186 (E2 exception model) | 81 |
| #188 (script stdlib families) | 1164 |
| #201 (math and operator families) | 5 |
| #210 (oracle pinned to 3.14) | 0 |

Generated on ubuntu-24.04 by the workflow: two runs (A and B), `cmp A B` byte-identical, header oracle as printed by `oracle_version()`. The header carries the CPython micro version, so a runner micro bump reports exit 2 (stale header) and the baseline is then regenerated with the workflow's generate mode.

No `canonical.py` rule change was made. Host-constant sensitivity is tracked separately.
