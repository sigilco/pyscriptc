# Cloud agent handoff 7: finish the emit/llvm seam migration (#829)

New compiler only. Every task moves a remaining emit/llvm mixin to read IR records through the `emit/_ir.py` typed accessors, byte-identical output. Done so far on master: `_abi`, `_calls`, `_stmts` (in review), `_print`, `_core`, `_funcs`, `_driver` (#949, #950).

## Tasks (one PR per mixin, strictly in this order; rebase on master before each)

- E-1 `emit/llvm/_exprs.py`: measure with `rg -c '\["(kind|type|id|name)"\]|\.get\("' src/pyscriptc/emit/llvm/_exprs.py`. Add accessors to `_ir.py` in a new section at the END of the file (this avoids merge conflicts with sibling PRs), then switch the reads.
- E-2 `emit/llvm/_stdlib.py`: same method.
- E-3 `emit/llvm/_runtime.py` and `_base.py`: same method; skip a file with zero raw reads and say so.
- E-4 inventory PR last: list what raw dict reads remain anywhere under `emit/` (count by file) as a note under the notes area, docs only.

## Method per mixin

1. Count raw reads before and after; the PR body shows both numbers.
2. One accessor per record field, typed return, docstring one line, a unit test in `tests/test_emitter_package.py` for each new accessor group.
3. Output must stay byte identical: `tests/test_emitter_goldens.py` with no re-bless.
4. Repin the census with `scripts/repin_census.py` if a count moved.

## Rules for every task

- Read `AGENTS.md` first; obey its guard rules. One branch and PR per task, base `master`, milestone `v0.9.0` (set it if your environment allows, otherwise say so in the PR), body ends `Refs #817`. Never push to master, tag or release. No co-author trailers (the repo forbids them; do not let your tooling add `Co-authored-by`). Conventional atomic commits.
- No GitHub issue or board edits. Do not edit frozen gate files (`benchmarks/compat2/outcomes.py`, `score.py`, `coverage_map.py`, `cache.py`, `calibrate.py`, `CALIBRATION.json`, `TRIAGE.md`, `probes/*.jsonl`, `probes/IR_HASHES.json`, `probes/TIR_HASHES.json`, `canonical.py` rules). If a change would alter an IR or TIR hash, stop and write the blocker in the PR.
- Gates before every commit: `uv run ruff check`, `uv run ruff format --check`, `tests/test_emitter_goldens.py` (byte identical, no re-bless), `tests/test_mir_leaf.py`, `compiles_itself`, `no_import_cycle`, `tests/test_census_self.py`. Census pins are per module (`tests/census_pins.json`); repin with `scripts/repin_census.py` only when a count moves, never hand-edit.
- No absolute home paths in repo notes. Docs follow the policy in AGENTS.md (divergences and gaps, not feature lists).
- Measure before you start: each group says what to count (with `rg`) so you size the work from the code, not from this note. If a task is already done on master, skip it and say so.
- Merge ordering: the orchestrator merges PRs one at a time and may ask you to rebase. Keep each PR one concern.
- A task that does not finish in an hour: write what remains in the PR and stop. Do not start anything not on the list.
