# Cloud agent handoff 9: dict-shape fence reduction in the remaining high-count modules

New compiler only. Census pins show the largest remaining fence counts. Each task reduces fences in one module by reading typed nodes instead; no behaviour change.

Measure first with `python scripts/compiler_progress.py --json` and `tests/census_pins.json`. Current pins at handoff: `cli._main` 553, `ffi_manifest` 122, `typefacts` 75, `check` 66, `factsbridge` 60, `tirserde` 53, `stubs` 49, `package` 47, `imports` 44.

## Tasks (one PR per module; independent files)

- F-0 first PR: a note ranking these modules by fence count and by the kind of fence (dict read, dict build, keyword-arg, tuple `in`), with the cheapest wins named. Docs only.
- F-1 `cli/_main.py`: split by fence kind and take only the mechanical ones (keyword-arg and `in`-tuple fences) in this PR. Do not restructure commands.
- F-2 `ffi_manifest.py`, F-3 `check.py`, F-4 `factsbridge.py`, F-5 `stubs.py`, F-6 `package.py` and `imports.py`: same method, mechanical fences only.
- Skip `coro.py` (covered by the typed-coro design) and `tirserde.py` (the serialize inventory #976 decides its shape).

## Method per module

1. Count fences before and after with the census tool; PR body shows both.
2. Mechanical rewrites only: `set[str]` for closed name sets, typed accessors from `emit/_ir.py` or the typed HIR, no new helpers unless two call sites need it.
3. Repin with `scripts/repin_census.py`; the pin must go down, never up.

## Rules for every task

- Read `AGENTS.md` first; obey its guard rules. One branch and PR per task, base `master`, milestone `v0.9.0` (set it if your environment allows, otherwise say so in the PR), body ends `Refs #817`. Never push to master, tag or release. No co-author trailers (the repo forbids them; do not let your tooling add `Co-authored-by`). Conventional atomic commits.
- No GitHub issue or board edits. Do not edit frozen gate files (`benchmarks/compat2/outcomes.py`, `score.py`, `coverage_map.py`, `cache.py`, `calibrate.py`, `CALIBRATION.json`, `TRIAGE.md`, `probes/*.jsonl`, `probes/IR_HASHES.json`, `probes/TIR_HASHES.json`, `canonical.py` rules). If a change would alter an IR or TIR hash, stop and write the blocker in the PR.
- Gates before every commit: `uv run ruff check`, `uv run ruff format --check`, `tests/test_emitter_goldens.py` (byte identical, no re-bless), `tests/test_mir_leaf.py`, `compiles_itself`, `no_import_cycle`, `tests/test_census_self.py`. Census pins are per module (`tests/census_pins.json`); repin with `scripts/repin_census.py` only when a count moves, never hand-edit.
- No absolute home paths in repo notes. Docs follow the policy in AGENTS.md (divergences and gaps, not feature lists).
- Measure before you start: each group says what to count (with `rg`) so you size the work from the code, not from this note. If a task is already done on master, skip it and say so.
- Merge ordering: the orchestrator merges PRs one at a time and may ask you to rebase. Keep each PR one concern.
- A task that does not finish in an hour: write what remains in the PR and stop. Do not start anything not on the list.
