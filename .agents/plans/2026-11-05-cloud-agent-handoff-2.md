# Cloud agent handoff 2: self-host fences, typed-HIR passes, observability

Date: 2026-11-05. Epic: #817. Milestone: v0.9.0. This is the second batch; it touches different files from `2026-11-05-cloud-agent-handoff.md` (batch 1: emit seam B5 to B8, A/B harness B9, coverage metric A12/D6, stdlib C6, spike S1) and from the local sessions (A1 MIR None returns, C1 to C4 runtime parity, B3/B4 emit `_abi`/`_calls`, B1/B2 in #939).

## Rules for every task

Same as batch 1: read `AGENTS.md` first; one branch and PR per task, base `master`, milestone `v0.9.0`, body ends `Refs #817`; never push to master, tag or release; no co-author trailers; conventional atomic commits; do not edit frozen gate files (`benchmarks/compat2/outcomes.py`, `score.py`, `coverage_map.py`, `cache.py`, `calibrate.py`, `CALIBRATION.json`, `TRIAGE.md`, `probes/*.jsonl`, `probes/IR_HASHES.json`, `probes/TIR_HASHES.json`, `canonical.py` rules); no GitHub issue, milestone or board edits; no absolute home paths in repo notes. Gates: `uv run ruff check`, `uv run ruff format --check`, the tests named per task, and for anything touching the lowerer or emit seam `tests/test_emitter_goldens.py` (byte identical), `tests/test_mir_leaf.py`, `compiles_itself`, `no_import_cycle`, `tests/test_census_self.py`. Census pins are per module (`tests/census_pins.json`); repin with the repin script only when a count moves, and say so in the PR.

If a change would alter an IR or TIR hash, stop: that needs a recorded decision and the A/B repin ritual (`python -m benchmarks.compat2.irscore` twice, `cmp`). Write the blocker in the PR instead of editing the hash files.

## Tasks (independent, at most one hour each)

### U2 self-host lowerer resolves PEP 585 `dict[str, Any]` like `typing.Dict`
The largest single root fence family (`dynamic-annotation`, about 812 self-host fences) may be one lowerer fix. Find where the lowerer resolves the typing alias form and make the builtin generic form (`dict`, `list`, `set`, `tuple`, `frozenset` subscripts with `Any`) resolve the same way. Follow `docs/guide/typing.md` and `.agents/docs/python-style.md`: annotations are the lowering input, a typing name used as a value stays a `PS2002` fence. Update the typing construct table in the same PR. Done when the self-host census fence count drops, the pins are repinned, and goldens are unchanged.

### V-F1 fences outside the pipeline: `cli/_main.py` and `cli/__init__.py`
Twelve modules outside `lower/` hold about 1,320 of the self-host fences. Reduce them by annotating or restructuring the offending code, never by suppressing the census. One PR per module. Done per module when its census pin drops and tests are green. Use `pyscriptc compile FILE --debug=fences` to see the fence list.

### V-F2 fences: `deps.py`, `typefacts.py`, `ffi_manifest.py`
Same method as V-F1, one PR per module.

### V-F3 fences: `config.py`, `embed.py`, `imports.py`
Same method, one PR per module.

### V-F4 fences: `verify.py`, `factsbridge.py`, `stubs.py`, `tirserde.py`
Same method, one PR per module.

### V-P1 passes read typed HIR: `irverify`
Convert `irverify` to read typed nodes instead of dict keys, mirroring the emit seam pattern (accessors, then a seam test that asserts no raw node-key reads). Done when the module has no raw reads and the verifier tests are green with identical diagnostics.

### V-P2 passes read typed HIR: check and validate
Same as V-P1 for the check pass and the validate pass, one PR each.

### V-O1 observability: tracker trend in the CI summary
Make the seam tracker trend (`scripts/compiler_progress.py`) print into the CI job summary on pull requests. Do not add the slice-coverage metric (batch 1, A12/D6 owns it); leave a clearly marked row for it. Done when a PR run shows the table in the job summary.

### V-O2 observability: #820 digests
Read issue #820 and implement only the digest output it asks for, as a tracked script plus test. If the issue is ambiguous, write the question in the PR and stop.

## Not in this batch (blocked or owned elsewhere)

A2 to A12 (MIR calls, short-circuit, value types, try CFG) wait on A1 and on the owner signature for the contract amendment (PR #937). B11, B12 (the MIR adapter and the default flip) wait on the A/B harness (batch 1, B9) and the decisions. D7 (GitHub setup) stays with the owner session.

## Order and conflicts

Fence tasks each touch one module and its census pin, so they merge in any order; if two PRs conflict on `tests/census_pins.json`, rebase and repin, do not hand-edit counts. U2 touches the lowerer and may move many pins; land it alone and rebase the rest after it.

## Reporting

Each PR carries: gates run, census pins moved, anything blocked. If a task cannot finish inside its hour, write what remains and stop.
