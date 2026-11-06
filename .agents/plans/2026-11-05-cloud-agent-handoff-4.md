# Cloud agent handoff 4: the other consumers of the dict IR read typed modules (drop package P6)

Date: 2026-11-05. Epic: #817. Milestone: v0.9.0. New-compiler-only: every task moves a dict-IR consumer onto the typed HIR (`_irnode.py`). Nothing here adds surface written against the dict IR.

In flight elsewhere (do not touch): emit seam (`emit/llvm/*`, `emit/_ir.py`, PRs #942, #947 to #950), `irverify`/validate (#952, #953), MIR files (`mir*.py`, session A2 adds `MIR_CALL`), `lower/`, `restlower/` (handoff 3), `canonical.py`, `ir.py`.

## What to build

After the drop, no module outside the lowering boundary reads the dict IR. Today these still do: the report and coverage surfaces, package merge, project loading, assess, reduce, bindings, bundling and similar. Convert them to read typed modules (or typed accessors over the lowered module), one module per PR.

## First step (one PR, do first)

Measure and publish the inventory as a repo note under the notes area: for each of `cli/_report.py`, `coverage.py`, `package.py`, `project.py`, `assess.py`, `reduce.py`, `binding.py`, `emitlib.py`, `bundle.py`, `boxed.py`, `dwarf.py`, `reflection.py`, `mobile.py`, `llvverify.py` count the raw dict-IR reads (`["kind"]`, `.get("kind")`, `["functions"]`, `["classes"]`, `["body"]`, `["span"]` on IR nodes) with `rg`, and say which entry points produce and which consume the dict. Rank by count. Docs only. Later tasks follow its ranking.

## Tasks (one PR per module, ordered by the inventory; independent files)

- P6-a `cli/_report.py` and the report surface (`--emit` report fields, `--debug` summaries) reading typed modules.
- P6-b `coverage.py`.
- P6-c `package.py` (package merge): the merge works on typed modules; keep the on-disk shape unchanged.
- P6-d `project.py`.
- P6-e `assess.py`, then `reduce.py`.
- P6-f `binding.py`, `emitlib.py`, `bundle.py`, `boxed.py` (boxed marker rules apply: boxed-only branches stay inside `boxed:begin`/`boxed:end` markers).

## Method per module

1. Find the producer of the data the module reads. If a typed accessor exists in `_irnode.py` or `emit/_ir.py`, use it; add accessors only where missing and put them beside their family.
2. Convert reads; keep outputs byte identical (tests that snapshot JSON, `--emit ir` output, reports).
3. Add a seam test asserting the module has no raw dict-IR reads (see `tests/test_emitter_package.py` for the style).
4. Census pin moves only if the count moves.

Done per module: no raw reads left, outputs unchanged, gates green.

## Rules for every task

- Read `AGENTS.md` first; obey its guard rules. One branch and PR per task, base `master`, milestone `v0.9.0` (set it if your environment allows, otherwise say so in the PR), body ends `Refs #817`. Never push to master, tag or release. No co-author trailers (the repo forbids them; do not let your tooling add `Co-authored-by`). Conventional atomic commits.
- No GitHub issue or board edits. Do not edit frozen gate files (`benchmarks/compat2/outcomes.py`, `score.py`, `coverage_map.py`, `cache.py`, `calibrate.py`, `CALIBRATION.json`, `TRIAGE.md`, `probes/*.jsonl`, `probes/IR_HASHES.json`, `probes/TIR_HASHES.json`, `canonical.py` rules). If a change would alter an IR or TIR hash, stop and write the blocker in the PR.
- Gates before every commit: `uv run ruff check`, `uv run ruff format --check`, `tests/test_emitter_goldens.py` (byte identical, no re-bless), `tests/test_mir_leaf.py`, `compiles_itself`, `no_import_cycle`, `tests/test_census_self.py`. Census pins are per module (`tests/census_pins.json`); repin with `scripts/repin_census.py` only when a count moves, never hand-edit.
- No absolute home paths in repo notes. Docs follow the policy in AGENTS.md (divergences and gaps, not feature lists).
- Measure before you start: each group says what to count (with `rg`) so you size the work from the code, not from this note. If a task is already done on master, skip it and say so.
- Merge ordering: the orchestrator merges PRs one at a time and may ask you to rebase. Keep each PR one concern.
- A task that does not finish in an hour: write what remains in the PR and stop. Do not start anything not on the list.
