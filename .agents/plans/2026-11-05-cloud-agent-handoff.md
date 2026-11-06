# Cloud agent handoff: parallel compiler work for v0.9.0

Date: 2026-11-05. Epic: #817 (re-architected compiler). Milestone: v0.9.0. Parent plan: the typed HIR + MIR compiler becomes the default by v0.9.0 and the dict IR is dropped by v0.10.0. This file lists only the tasks that do not overlap the sessions already running locally.

## Already taken (do not touch)

- A1 MIR `None` returns (`mir.py`, `mirlower.py`, `miremit.py`, `mirverify.py`): local session `a1-none-returns`.
- C1 to C4 runtime parity in `ps_help.c` and `ps_osmore.c` (#924): local session `c-int-parity`.
- B3, B4 emit seam for `emit/llvm/_abi.py` and `_calls.py`: local session `b3-emit-abi-calls`.
- B1, B2 (`_exprs.py`, accessors in `emit/_ir.py`): PR #939. Rebase on it once merged; never duplicate accessors.
- D1 (hash-seed ordering): merged in #936. D4, D5 decision drafts: PR #937 (owner signature pending, do not edit).

## Rules for every task

- Read `AGENTS.md` first and obey its guard rules. One branch and one PR per task, base `master`, milestone `v0.9.0`, body ends with `Refs #817`.
- Master is PR only. Never tag, release, or push to master. No co-author trailers. Conventional atomic commits, one commit after each verified unit.
- Do not edit frozen gate files: `benchmarks/compat2/outcomes.py`, `score.py`, `coverage_map.py`, `cache.py`, `calibrate.py`, `CALIBRATION.json`, `TRIAGE.md`, `probes/*.jsonl`, `probes/IR_HASHES.json`, `canonical.py` rules.
- Do not do GitHub issue, milestone or board edits (task D7 belongs to the owner session).
- Gates before every commit: `uv run ruff check`, `uv run ruff format --check`, and the tests named in the task. Common set for emit work: `tests/test_emitter_goldens.py` (byte identical, no re-bless), `tests/test_mir_leaf.py`, `compiles_itself`, `no_import_cycle`, `tests/test_census_self.py`. Run the CI selection with `uv run pytest -n auto -m "not linux_container" --ignore=tests/compat2`.
- Census pins are per module (`tests/census_pins.json`); repin with the repin script only when the count moves.
- Write nothing with absolute home paths into repo notes.

## Tasks (independent; each is at most one hour)

### B5 emit seam: `emit/llvm/_stmts.py`
Convert every raw dict-key read on an IR node to the `emit/_ir.py` accessors, following the pattern of PR #939. Add a seam test next to the existing ones. Done when the module has no raw node-key reads and the common gates pass.

### B6 emit seam: `emit/llvm/_stdlib.py`
Same as B5.

### B7 emit seam: `emit/llvm/_print.py`
Same as B5.

### B8 emit seam: `emit/llvm/_core.py`, `_funcs.py`, `_driver.py`
Same as B5. One PR for the three small modules.

### B9 runtime A/B differential harness (the flip's release gate)
Build a tracked tool and test that compiles a program through the direct emitter lane and the opt-in MIR lane, runs both binaries in isolated temp dirs, and diffs stdout, stderr and return code. Start with the MIR leaf fixtures under `tests/fixtures/mir_leaf/` and the programs where `supported_fn` holds. Tests must skip cleanly when no C toolchain is present. Done when `uv run pytest` runs the harness test green and the tool prints a per-program verdict table. Do not change either lane.

### A12 / D6 slice coverage metric
Add `scripts/mir_coverage.py`: for each corpus program in the visible corpus (`benchmarks/compat2/corpus`, never any other split), run `lower_file` and `supported_fn` per function and print in-slice over total non-fenced functions, with a per-script breakdown. Then add the percentage to the trend table in `scripts/compiler_progress.py`. Baseline measured on 2026-11-05 over 20 scripts: 3 of 22 functions, 14 percent. Done when the script runs deterministically (twice, `cmp`) and the metric appears in the trend table.

### S1 spike: can the FFI data plane build on the MIR-default compiler?
Deliverable is one page in a PR (`docs/` is off limits; put it under the repo notes area used by other spikes) with a verdict and one FFI probe compiled on both lanes. Do not change compiler code. Context: #240 stays in v0.9.0 only if the verdict is yes.

## Order and conflicts

B5 to B8 touch separate files and merge in any order. B9 and A12 are new files. If a PR touches `emit/_ir.py`, rebase on master first because #939 and later seam PRs add accessors there; keep accessor additions alphabetically near their family so merges stay trivial.

## Reporting

For each PR leave a short note with: gates run, census pins moved or not, and anything blocked. Stop and write the blocker instead of extending scope.

## Deferred

C6 (stdlib surface for the patch release) is deferred: new surface written against the dict IR widens the gap between the old and new architectures. It returns after the default flip.
