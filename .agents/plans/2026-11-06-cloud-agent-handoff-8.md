# Cloud agent handoff 8: MIR tooling and telemetry that does not touch the lowering critical path

New compiler only. The MIR lowering ops (A2 to A8) are being written elsewhere: do not edit `mirlower.py`, `miremit.py` or `mir.py` op definitions. Touch `mirverify.py` only to add tests. Everything here reads or wraps the MIR.

## Tasks (independent PRs)

- T-1 `pyscriptc compile FILE --emit mir`: a stable textual MIR printer (a new module, e.g. `mirprint.py`) plus golden tests over three leaf programs. Measure first how MIR is dumped today (`rg 'def .*dump|repr' src/pyscriptc/mir*.py`); if a printer exists, harden it with goldens instead of writing a new one.
- T-2 Out-of-slice reasons report: extend `scripts/compiler_progress.py` (the `mir_coverage` block) with a table of why each function is outside the MIR slice (unsupported op kind, count per reason, top 10). Source of truth is the same slice predicate the coverage metric uses; do not fork the predicate. Add a trend row, update its test.
- T-3 Differential smoke test: for every corpus program already marked in the MIR slice, a pytest that compiles direct and via `PYSC_MIR` and asserts identical `.ll` per function (reuse `test_mir_leaf` helpers, no new gate semantics). Mark it so it runs in the default selection only if under 60 seconds, otherwise behind an existing slow marker.
- T-4 `PYSC_MIR` escape hatch docs: a short entry in the divergences or known-gaps doc (policy: say what differs or fails, not what works), plus `.agents/docs/` agent note on how to flip it per function.
- T-5 Adapter telemetry design note (B11): what to log when a function falls back to the typed-HIR adapter (function, reason, module), where it surfaces (`--debug` stages table, JSONL log), and the cost. Notes only.

## Rules for every task

- Read `AGENTS.md` first; obey its guard rules. One branch and PR per task, base `master`, milestone `v0.9.0` (set it if your environment allows, otherwise say so in the PR), body ends `Refs #817`. Never push to master, tag or release. No co-author trailers (the repo forbids them; do not let your tooling add `Co-authored-by`). Conventional atomic commits.
- No GitHub issue or board edits. Do not edit frozen gate files (`benchmarks/compat2/outcomes.py`, `score.py`, `coverage_map.py`, `cache.py`, `calibrate.py`, `CALIBRATION.json`, `TRIAGE.md`, `probes/*.jsonl`, `probes/IR_HASHES.json`, `probes/TIR_HASHES.json`, `canonical.py` rules). If a change would alter an IR or TIR hash, stop and write the blocker in the PR.
- Gates before every commit: `uv run ruff check`, `uv run ruff format --check`, `tests/test_emitter_goldens.py` (byte identical, no re-bless), `tests/test_mir_leaf.py`, `compiles_itself`, `no_import_cycle`, `tests/test_census_self.py`. Census pins are per module (`tests/census_pins.json`); repin with `scripts/repin_census.py` only when a count moves, never hand-edit.
- No absolute home paths in repo notes. Docs follow the policy in AGENTS.md (divergences and gaps, not feature lists).
- Measure before you start: each group says what to count (with `rg`) so you size the work from the code, not from this note. If a task is already done on master, skip it and say so.
- Merge ordering: the orchestrator merges PRs one at a time and may ask you to rebase. Keep each PR one concern.
- A task that does not finish in an hour: write what remains in the PR and stop. Do not start anything not on the list.
