# Cloud agent handoff 6: the MIR adapter, the nightly lane, and the release receipt tooling

Date: 2026-11-05. Epic: #817. Milestone: v0.9.0. New-compiler-only: builds the pieces the default flip needs that are not MIR op work: the adapter that picks MIR or the typed path per function, the nightly lanes, and the tooling that produces the release receipts.

In flight elsewhere (do not touch): MIR op work (session A2 owns `mir.py`, `mirlower.py`, `miremit.py`, `mirverify.py` and the call op; A6/A7 follow it), emit seam PRs (#942, #947 to #950, they touch `emit/_ir.py` and `emit/llvm/*`), the A/B harness (#943) and the coverage metric (#944). Rebase onto those once merged; do not duplicate their files.

The default flip itself and its decisions belong to the owner (draft PR #937). Everything here keeps the current default; the MIR path stays opt-in.

## Tasks

- B11 `emit/_mir_in.py` adapter (start after #950 merges, since it edits the emit driver call site). Per function: expose MIR when `supported_fn` holds, otherwise the typed path, behind one call site and an env switch that defaults to OFF. Per-function output must stay byte identical to the direct emitter for the fixtures in `tests/fixtures/mir_leaf/`. Include unit tests for the selection logic and a fallback test (a function outside the slice goes the typed way).
- B12-prep the escape hatch contract (docs plus a test, no behavior change): write down the env names for "MIR on", "MIR off" and the inverted default for the flip, add a test that asserts today's default and that both switches work, and list where the default is read. The flip is a one-line change after the owner signs the decision.
- D2 wire the A/B harness (PR #943) into the nightly summary as a lane that publishes per-program verdicts; start after #943 merges. No threshold changes; report only.
- Receipt-1 perf lane: a script that compiles and times a fixed set of kernel/stdlib/scripting programs on the direct lane and the MIR lane and prints a ratio table (median of N runs, N configurable, noise reported). Output JSON plus a markdown table; no CI gate yet. Reuse `benchmarks/baseline` machinery if it fits; do not edit its frozen baseline.
- Receipt-2 compat no-flip tool: a script that takes two compat scorer result files and reports outcome flips in both directions per row (the v0.8.0 receipt format). Read the existing receipt notes for the exact shape. It must not read or require any closed corpus split.

## Done per task

Gates green, new tests for new logic, the MIR path still opt-in, the default unchanged, no frozen file touched.

## Rules for every task

- Read `AGENTS.md` first; obey its guard rules. One branch and PR per task, base `master`, milestone `v0.9.0` (set it if your environment allows, otherwise say so in the PR), body ends `Refs #817`. Never push to master, tag or release. No co-author trailers (the repo forbids them; do not let your tooling add `Co-authored-by`). Conventional atomic commits.
- No GitHub issue or board edits. Do not edit frozen gate files (`benchmarks/compat2/outcomes.py`, `score.py`, `coverage_map.py`, `cache.py`, `calibrate.py`, `CALIBRATION.json`, `TRIAGE.md`, `probes/*.jsonl`, `probes/IR_HASHES.json`, `probes/TIR_HASHES.json`, `canonical.py` rules). If a change would alter an IR or TIR hash, stop and write the blocker in the PR.
- Gates before every commit: `uv run ruff check`, `uv run ruff format --check`, `tests/test_emitter_goldens.py` (byte identical, no re-bless), `tests/test_mir_leaf.py`, `compiles_itself`, `no_import_cycle`, `tests/test_census_self.py`. Census pins are per module (`tests/census_pins.json`); repin with `scripts/repin_census.py` only when a count moves, never hand-edit.
- No absolute home paths in repo notes. Docs follow the policy in AGENTS.md (divergences and gaps, not feature lists).
- Measure before you start: each group says what to count (with `rg`) so you size the work from the code, not from this note. If a task is already done on master, skip it and say so.
- Merge ordering: the orchestrator merges PRs one at a time and may ask you to rebase. Keep each PR one concern.
- A task that does not finish in an hour: write what remains in the PR and stop. Do not start anything not on the list.
