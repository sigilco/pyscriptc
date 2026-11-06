# Cloud agent handoff 11: v0.10.0 drop preparation (old compiler removal), new-compiler-side work only

New compiler only. Nothing here deletes the old path; it prepares the deletion so v0.10.0 is a mechanical PR. Depends on #959 (P2 pilot) merged for P2-1.

## Tasks (independent PRs unless noted)

- DR-1 Deletion map: a note listing every module, test and CI job that exists only for the dict IR path, with the line count and the PR that would remove it (use the seam tracker and `rg`). Docs only.
- DR-2 P2-1 `restlower` typed nodes for the next modules after the pilot (#959): pick by fence count from `tests/census_pins.json`, one module per PR, exclude `restlower/_system.py` and `restlower/__init__.py`.
- DR-3 Test migration continued: tests that still assert on dict IR shapes per the inventory (#968); move the next ten files to typed accessors, one PR per ten files.
- DR-4 Retirement of `IR_HASHES.json` follow-through: implement only the safe part of the plan in #980/#973 that does not alter frozen gates (a doc cross-link and a test that TIR covers every probe IR hash covers). Stop and write the blocker if a frozen file would change.

## Rules for every task

- Read `AGENTS.md` first; obey its guard rules. One branch and PR per task, base `master`, milestone `v0.9.0` (set it if your environment allows, otherwise say so in the PR), body ends `Refs #817`. Never push to master, tag or release. No co-author trailers (the repo forbids them; do not let your tooling add `Co-authored-by`). Conventional atomic commits.
- No GitHub issue or board edits. Do not edit frozen gate files (`benchmarks/compat2/outcomes.py`, `score.py`, `coverage_map.py`, `cache.py`, `calibrate.py`, `CALIBRATION.json`, `TRIAGE.md`, `probes/*.jsonl`, `probes/IR_HASHES.json`, `probes/TIR_HASHES.json`, `canonical.py` rules). If a change would alter an IR or TIR hash, stop and write the blocker in the PR.
- Gates before every commit: `uv run ruff check`, `uv run ruff format --check`, `tests/test_emitter_goldens.py` (byte identical, no re-bless), `tests/test_mir_leaf.py`, `compiles_itself`, `no_import_cycle`, `tests/test_census_self.py`. Census pins are per module (`tests/census_pins.json`); repin with `scripts/repin_census.py` only when a count moves, never hand-edit.
- No absolute home paths in repo notes. Docs follow the policy in AGENTS.md (divergences and gaps, not feature lists).
- Measure before you start: each group says what to count (with `rg`) so you size the work from the code, not from this note. If a task is already done on master, skip it and say so.
- Merge ordering: the orchestrator merges PRs one at a time and may ask you to rebase. Keep each PR one concern.
- A task that does not finish in an hour: write what remains in the PR and stop. Do not start anything not on the list.
