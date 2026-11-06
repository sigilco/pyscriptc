# Cloud agent handoff 5: tests off the dict IR, and the retirement path for IR_HASHES.json, canonical.py and coro.py

Date: 2026-11-05. Epic: #817. Milestone: v0.9.0. New-compiler-only: prepares drop packages P3 and P4 and moves the test suite off the dict IR so the old shape can be deleted.

In flight elsewhere (do not touch): emit seam PRs (#942, #947 to #950), MIR files (session A2), `lower/`, `restlower/` (handoff 3), dict-IR consumers (handoff 4), `canonical.py`/`ir.py` source (design only here).

## Group T: tests stop asserting on dict IR shapes

About 39 test files mention `"kind"`. Tests that assert on the dict IR pin the old architecture and block its deletion.

- T-0 (first, docs only, one PR): inventory. For every test file that reads the dict IR (`rg -l '"kind"' tests/ | sort`), classify each use: (a) could assert on behavior or the typed module instead, (b) pins a dict shape on purpose (canonical, IR hash, serialization), (c) a fixture. Write the table in the repo notes area. Later tasks follow it.
- T-1 migrate class (a) uses to typed accessors or behavior assertions, grouped by area, at most 6 files per PR. The assertion must stay as strong; do not weaken a test to make it pass. Names of tests may not change.
- Class (b) stays; the design notes below say when it retires.

## Group R: design notes (docs only, no code, no frozen files touched)

Each note ends with a task breakdown of one-hour tasks and the owner decisions still open. Read `2026-11-02-mir-backend-only-contract.md`, `2026-11-02-tir-gate-streaming-serializer.md` and the decision drafted in PR #937 first.

- R-1 (#830) retirement of `IR_HASHES.json` and the dict-shape canonical rules: what the TIR streaming gate already covers and what it does not, the exact order of removal (gate, `canonical.py` rules, `ir.py`, hash files), what CI jobs and the nightly change, and the evidence required (the nightly clock: 14 consecutive green nightly-gates runs, 1 so far).
- R-2 (P3) `coro.py` and generators: today generators stay on the dict path through the adapter (`lower/_coro_adapter.py`). Describe what a typed coroutine lowering needs (frame synthesis, send/throw/close semantics), whether the adapter can be reduced to a thin typed shim before the drop, and the size of the work in one-hour tasks. Cover `coro.py` (about 95 KB) honestly: which parts are IR-shape and which are semantics.
- R-3 inventory of every module that still serializes or deserializes the dict IR (`node_to_dict`, `dict_to_node`, JSON on disk, caches such as the pure-dep IR cache): the format each uses and what a typed on-disk format would require.

## Order

T-0, R-1, R-2, R-3 are independent docs PRs; start with T-0 and R-1. T-1 follows T-0.

## Rules for every task

- Read `AGENTS.md` first; obey its guard rules. One branch and PR per task, base `master`, milestone `v0.9.0` (set it if your environment allows, otherwise say so in the PR), body ends `Refs #817`. Never push to master, tag or release. No co-author trailers (the repo forbids them; do not let your tooling add `Co-authored-by`). Conventional atomic commits.
- No GitHub issue or board edits. Do not edit frozen gate files (`benchmarks/compat2/outcomes.py`, `score.py`, `coverage_map.py`, `cache.py`, `calibrate.py`, `CALIBRATION.json`, `TRIAGE.md`, `probes/*.jsonl`, `probes/IR_HASHES.json`, `probes/TIR_HASHES.json`, `canonical.py` rules). If a change would alter an IR or TIR hash, stop and write the blocker in the PR.
- Gates before every commit: `uv run ruff check`, `uv run ruff format --check`, `tests/test_emitter_goldens.py` (byte identical, no re-bless), `tests/test_mir_leaf.py`, `compiles_itself`, `no_import_cycle`, `tests/test_census_self.py`. Census pins are per module (`tests/census_pins.json`); repin with `scripts/repin_census.py` only when a count moves, never hand-edit.
- No absolute home paths in repo notes. Docs follow the policy in AGENTS.md (divergences and gaps, not feature lists).
- Measure before you start: each group says what to count (with `rg`) so you size the work from the code, not from this note. If a task is already done on master, skip it and say so.
- Merge ordering: the orchestrator merges PRs one at a time and may ask you to rebase. Keep each PR one concern.
- A task that does not finish in an hour: write what remains in the PR and stop. Do not start anything not on the list.
