# Python version support window

Assessment for the CPython versions pyscriptc declares, oracles against, and
plans to target with `--target-version`. Sources: [endoflife.date
python](https://endoflife.date/python) (queried 2026-10-17), the release PEPs,
and the repo's own files (`pyproject.toml`, `docker/Dockerfile`,
`benchmarks/compat2/probes/`, `docs/version-track.md`, `README.md`).

## CPython lifecycle (verified)

| Version | Release | Security support ends | PEP | Status today |
| --- | --- | --- | --- | --- |
| 3.10 | 2021-10-04 | **2026-10-01** | [PEP 619](https://peps.python.org/pep-0619/) | **EOL — already dead**, not "EOL this month" |
| 3.11 | 2022-10-24 | 2027-10-31 | [PEP 664](https://peps.python.org/pep-0664/) | security-only (~12 months left) |
| 3.12 | 2023-10-02 | 2028-10-31 | [PEP 693](https://peps.python.org/pep-0693/) | security-only; **primary oracle** |
| 3.13 | 2024-10-07 | 2029-10-31 | [PEP 719](https://peps.python.org/pep-0719/) | bugfix until 2026-10-01, now security-only |
| 3.14 | 2025-10-07 | 2030-10-31 | [PEP 745](https://peps.python.org/pep-0745/) | bugfix until 2027-10; what CI runs |
| 3.15 | 2026-10-09 | 2031-10-31 | [PEP 790](https://peps.python.org/pep-0790/) | **final released 2026-10-09**, no longer dev |

Two corrections to the premise worth noting: 3.10 did not merely reach EOL
this month — it passed end-of-life on 2026-10-01, sixteen days before this
assessment. And 3.15.0 shipped eight days ago, so "3.15 (dev)" is stale; it is
a real version on endoflife.date's cycle list with EOL 2031-10-31.

## What each supported version costs today

The version axis touches the repo in these places:

- `pyproject.toml`: `requires-python = ">=3.10"`, five classifiers (3.10–3.14).
- Oracle installs: `docker/Dockerfile` runs
  `uv python install 3.10.22 3.11.17 3.12.15 3.13.16 3.14.8` into
  `PYSC_ORACLE_DIR`; `~/oracles` on dev boxes mirrors this. Each axis version
  is one pinned interpreter install to keep updated.
- Probe axis: `probes/generate.py:36` `AXIS = ("3.10", …, "3.14")`. Every row
  runs once per interpreter (plus a second-seed oracle pass) before its
  `versions` set is recorded; `probes/versions.py` must stay parseable by the
  oldest axis version. Manifest `versions.by_area` shows the deltas are small
  but real (e.g. surface probes valid: 11 543 on 3.10 → 12 597 on 3.12;
  builtins 394 → 403; language 430 → 440).
- Calibration / scoring: `probes/CALIBRATION.json` cells and `score.py`'s
  `counts_by_version` produce one column per axis version; the planned V-track
  scorer columns are weighted by a market snapshot.
- IR: `ir.py::cpython_version` stamps `2/cpython-<x.y.z>` on the module and
  `emit_llvm` hard-errors on a stamp≠running-interpreter mismatch
  (`VersionError`). Cache keys in `compat2/cache.py` embed the oracle version,
  so `IR_HASHES.json` regen is per-oracle, not per-axis-version — dropping an
  axis version does not change regen cost, which is already "run irscore twice
  and `cmp`" per oracle.
- Semantics: lowered behavior tracks CPython per call, so version-specific
  stdlib changes are real surface. `parse_qs` splits on `&` only "which is
  what CPython 3.14 does" (`README.md:121`) — the 3.13-and-earlier behavior
  differed. The typeshed `VERSIONS` gate (design in
  `docs/version-track.md`) must cover every name whose presence or semantics
  spans the window; a narrower window shrinks that table's edge cases
  (`tomllib` 3.11+, `annotationlib`/`templatelib` 3.14+).
- Host interpreter: the compiler parses with the *running* interpreter's
  `ast` and bakes island constants (`sys.version`, `sys.platform`, …) from it
  (`README.md:92`). `lower/__init__.py:1047` folds
  `if sys.version_info` against the running interpreter.
- CI: `.github/workflows/ci.yml` pins `python-version: "3.14"` everywhere —
  there is no host-version matrix today, so the declared window is only
  enforced by `requires-python`, not tested.

## Host version vs target version

The important split: pyscriptc emits **version-independent native binaries**.
The version axis only affects (a) which interpreter *runs the compiler* (host)
and (b) which CPython semantics the lowering oracle and `--target-version`
gate agree with (target). Compiled output carries neither a Python runtime
nor a version dependency — the `cpython` IR stamp is a consistency check, not
a runtime requirement.

That means the two windows can differ: the host window is a packaging/CI
question; the target window is an oracle-and-scorer question.

## Should the window drop 3.10 (or 3.11)?

- **(a) Realistic hosts.** Nobody should run a compiler on an interpreter
  that stopped receiving security fixes this month. `uv python install`
  makes "use a current CPython" trivial even on old distros, so a
  `>=3.12` host floor costs users almost nothing. Debian 12's stock 3.11
  is the only plausible straggler — and `uv` covers it too.
- **(b) Typeshed / `feature_version` gating.** The gate does not get
  structurally simpler at a 3.12 floor (the two-layer grammar+node table
  stays), but dropping 3.10 removes the only soft-keyword special case
  (match statements parse at every `feature_version`, so they need the
  AST-node gate rather than the grammar gate) and every remaining axis
  version then has at least one real grammar gate to test against.
- **(c) IR-hash / flipcheck regen.** Does not scale with the axis — regen is
  per-oracle (3.12 primary, 3.14 secondary), unchanged by dropping 3.10.
  The savings instead land in probe generation (one fewer interpreter pass
  over ~13k surface probes + smaller `versions` bookkeeping) and one fewer
  calibration/score column.
- **(d) Optics.** For a v0.1.0 README, "supports Python 3.10+" advertises an
  EOL interpreter; "3.12+" reads as current and matches the primary oracle.
  3.15 just released, so "3.12–3.15" also looks alive rather than trailing.

## Recommendation

**Drop 3.10 now; keep 3.11 as a best-effort target axis entry; make 3.12 the
hard floor for the host.**

- Host: `requires-python = ">=3.12"`. Matches the primary oracle, the
  default `--target-version`, and CPython's own active support window. A
  hard floor, not a warning: installing on 3.10/3.11 fails at pip resolution
  with a clear message; running a source checkout on <3.12 should fail early
  in `cli.main` with a version check naming the floor.
- Target axis: `AXIS = ("3.11", "3.12", "3.13", "3.14")`, default 3.12. 3.11
  stays because `except*` is a real gate and Debian 12 still ships it; it is
  best-effort — its scorer column exists but CI does not gate on it. Do **not**
  drop 3.11 yet: it has 12 months of upstream security support and the
  per-version cost is one interpreter install plus one scorer column.
- 3.15: add to the axis at the next oracle refresh (pin a `3.15.x` uv install,
  extend `AXIS`), not bundled with the 3.10 drop. `probes/versions.py`'s
  "parseable by 3.10" floor becomes 3.11.

### Migration checklist (separate PRs, doc only here)

1. `pyproject.toml`: `requires-python = ">=3.12"`; classifiers drop
   3.10/3.11, add 3.15.
2. `docker/Dockerfile`: drop `3.10.22` from the `uv python install`
   line (keep 3.11.17 while 3.11 is in the axis).
3. `benchmarks/compat2/probes/generate.py`: `AXIS` loses `"3.10"`;
   `probes/versions.py` docstring floor 3.10 → 3.11;
   `version_surface.py::TARGET_VERSIONS` range 10→11; regenerate `MANIFEST.json`
   (byte-identical discipline: regenerate twice, `cmp`).
4. `docs/version-track.md`: retarget `--target-version {3.11..3.14}`, update
   the node-gate table's 3.10 row (match gating moves to "below floor" notes).
5. `README.md` / `docs/supported.md`: state the 3.12 host floor and the
   3.11–3.14 target axis.
6. CI: add a second host-version leg (3.12 floor + 3.14 newest) so the floor
   is actually exercised; the macos-safe job can take 3.12.
7. `cli.main` (or `lower` entry): explicit early `sys.version_info < (3,12)`
   error for source checkouts.

### What breaks on 3.10

- `pip install pyscriptc` on CPython 3.10/3.11: **hard error** at resolution
  (`requires-python`). Nothing silently degrades.
- Running from a source checkout on <3.12: hard error at CLI entry (item 7).
- Files lowered on 3.10 earlier: their IR stamp `2/cpython-3.10.x` already
  fails `emit_llvm`'s stamp check on any supported host — no new failure mode.
- `--target-version 3.10`: gone from the axis; selecting it errors with the
  supported-set list. 3.10-targeted source (match-free, `tomllib`-free code)
  almost always also targets 3.11 unchanged, so the practical loss is nil.
