# Cloud agent handoff 10: flip readiness (everything the default flip needs except the MIR ops)

New compiler only. The MIR ops (A2 to A8) and the B11/B12 adapter wiring are on the orchestrator's local lane: do not edit `mir.py`, `mirlower.py`, `miremit.py`. This batch builds the gates and receipts the flip decision will read.

## Tasks (independent PRs)

- FR-1 CI lane: run the default test selection with the MIR path forced on (`PYSC_MIR` set to its on value) as a nightly-only job that reports without blocking. Measure how `PYSC_MIR` is read today (`rg PYSC_MIR src tests .github`) and reuse that knob; no new env var.
- FR-2 Runtime A/B corpus: extend the A/B harness (#943) inputs from the in-slice corpus functions to the 20 script programs, reporting per-program verdicts (same, differs, not in slice) in the nightly summary (#966 shape). No gate semantics change.
- FR-3 No-flip compat receipt tool: a script that compiles the visible corpus with MIR off and on and prints the outcome flips in both directions (build on `feat(compat): report outcome flips both directions`, #982). Output a markdown table for the release PR. Do not touch the frozen scorer files; call them read-only.
- FR-4 Flip checklist doc: `.agents/docs/release/mir-flip-checklist.md`: each criterion from the v0.9.0 milestone, the command that measures it and the threshold, so the owner reads one page. Pull thresholds from the milestone description, do not invent them.
- FR-5 Draft the v0.9.0 release notes skeleton under the notes area (Internal vs user-visible sections per the release policy). Draft only; no tags.

## Rules for every task

- Read `AGENTS.md` first; obey its guard rules. One branch and PR per task, base `master`, milestone `v0.9.0` (set it if your environment allows, otherwise say so in the PR), body ends `Refs #817`. Never push to master, tag or release. No co-author trailers (the repo forbids them; do not let your tooling add `Co-authored-by`). Conventional atomic commits.
- No GitHub issue or board edits. Do not edit frozen gate files (`benchmarks/compat2/outcomes.py`, `score.py`, `coverage_map.py`, `cache.py`, `calibrate.py`, `CALIBRATION.json`, `TRIAGE.md`, `probes/*.jsonl`, `probes/IR_HASHES.json`, `probes/TIR_HASHES.json`, `canonical.py` rules). If a change would alter an IR or TIR hash, stop and write the blocker in the PR.
- Gates before every commit: `uv run ruff check`, `uv run ruff format --check`, `tests/test_emitter_goldens.py` (byte identical, no re-bless), `tests/test_mir_leaf.py`, `compiles_itself`, `no_import_cycle`, `tests/test_census_self.py`. Census pins are per module (`tests/census_pins.json`); repin with `scripts/repin_census.py` only when a count moves, never hand-edit.
- No absolute home paths in repo notes. Docs follow the policy in AGENTS.md (divergences and gaps, not feature lists).
- Measure before you start: each group says what to count (with `rg`) so you size the work from the code, not from this note. If a task is already done on master, skip it and say so.
- Merge ordering: the orchestrator merges PRs one at a time and may ask you to rebase. Keep each PR one concern.
- A task that does not finish in an hour: write what remains in the PR and stop. Do not start anything not on the list.
