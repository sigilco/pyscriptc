# Cloud agent handoff 3: new-compiler-only work (restlower to typed nodes, MIR design, MIR gates)

Date: 2026-11-05. Epic: #817. Milestone: v0.9.0. Replaces the earlier draft of this batch (old-architecture stdlib, bug and bench work), which is deferred so the old and new architectures do not drift further apart. Every task here moves code onto the typed HIR or the MIR, or designs the next MIR step. No task adds new surface written against the dict IR.

Other work in flight (do not touch): batch 1 (`2026-11-05-cloud-agent-handoff.md`: emit seam B5 to B8, A/B harness B9, coverage metric A12/D6, spike S1), batch 2 (`-2.md`: U2, self-host fences, typed-HIR passes, observability), local sessions A1 (MIR None returns), C1 to C4 (runtime parity, edits `restlower/_system.py` and `restlower/__init__.py`), and the emit seam PRs.

## Rules for every task

- Read `AGENTS.md` first; obey its guard rules. One branch and PR per task, base `master`, milestone `v0.9.0`, body ends `Refs #817`. Never push to master, tag or release. No co-author trailers. Conventional atomic commits.
- No GitHub issue, milestone or board edits. Do not edit frozen gate files (`benchmarks/compat2/outcomes.py`, `score.py`, `coverage_map.py`, `cache.py`, `calibrate.py`, `CALIBRATION.json`, `TRIAGE.md`, `probes/*.jsonl`, `probes/IR_HASHES.json`, `probes/TIR_HASHES.json`, `canonical.py` rules). If a change would alter an IR or TIR hash, stop and write the blocker in the PR.
- Gates before every commit: `uv run ruff check`, `uv run ruff format --check`, `tests/test_emitter_goldens.py` (byte identical, no re-bless), `tests/test_mir_leaf.py`, `compiles_itself`, `no_import_cycle`, `tests/test_census_self.py`. Census pins are per module (`tests/census_pins.json`); repin with `scripts/repin_census.py` only when a count moves.
- No absolute home paths in repo notes.

## Group P2: `restlower/` builds typed nodes (drop package P2)

`restlower/` is 36 modules, about 6,100 lines and about 100 raw `["kind"]` reads. The seam tracker never counted it. The goal is that each module reads and builds typed `_irnode` values through accessors/builders instead of dict keys, with identical emitted IR.

- P2-0 (pilot, do first, merge before the rest): `_shared.py` plus one small module (`_time.py`). Establish the pattern: which accessors and builders to use, and a seam test (see `tests/test_emitter_package.py` for the style) asserting the converted modules have no raw node-key reads. Write the pattern in the PR description so later tasks follow it.
- P2-1 after P2-0 merges, one PR per module, any order: `_datetime`, `_pathlib`, `_copy`, `_textwrap`, `_csv`, `_dotenv`, `_hmac`, `_operator`, `_orjson`, `_ospath`, `_jwt`, `_random`.
- P2-2 one PR per module: `_collections`, `_functools`, `_subprocess`, `_io`, `_folds`, `_asyncio`, `_builtins`, `_queue`, `_urllib`.
- P2-3 larger modules, one PR each, only after P2-1 is mostly merged: `_minmax`, `_iterators`, `_asyncio_sync`, `_threading`, `_argparse`, `_itertools`.
- Excluded for now: `_system.py` and `restlower/__init__.py` (a local session is editing them; revisit after it merges).

Done per module: no raw node-key reads left, goldens byte identical, the IR for the module's tests unchanged (compare `--emit ir` before and after on the tests that exercise it), census pin moved only if the count moved.

## Group M: MIR design notes (docs only, no code)

Each is one note under the repo notes area used by other design notes, and ends with a concrete task breakdown of one-hour tasks plus the open decisions for the owner. Read `mir.py`, `mirlower.py`, `miremit.py`, `mirverify.py` and the decision `2026-11-02-mir-backend-only-contract.md` first. Measured baseline for context: 3 of 22 corpus functions are in the MIR slice today; 15 of 22 after None returns; about 18 of 22 after calls and short-circuit; 95 to 100 percent after str and list value types.

- M-1 (A8) the str value type: operand encoding beyond int-only refs (value refs vs const-pool indices), what changes in `mir.py` and the digest gate, and the repin ritual cost.
- M-2 (A9) list and dict value types, building on M-1.
- M-3 (A10) try and handlers as a block-argument CFG, including how it interacts with fences and exception codes.
- M-4 (A11) closures and env cells in MIR.
- M-5 the MIR digest gate: how MIR hashes are recorded beside `TIR_HASHES.json`, the regen ritual, and what the nightly checks. Propose; do not edit gate files.

## Group G: MIR gates and tests (new files only)

- G-1 negative tests for `mirverify.py`: one test per verifier rule, constructing a malformed MIR and asserting the diagnostic. New test file only; do not change `mirverify.py` unless a rule has no way to be reached, and then report instead.
- G-2 a determinism test for the MIR lane: lower and emit each `tests/fixtures/mir_leaf/` fixture under three `PYTHONHASHSEED` values and assert identical output (the pattern of `tests/test_ll_hashseed.py`).

## Out of scope

Old-architecture stdlib surface, bug-fix and bench-lane work (deferred). Anything under `lower/` (being split and fenced elsewhere). `mir.py`, `mirlower.py`, `miremit.py`, `mirverify.py` edits beyond what a task above names (A1 is in flight locally). The default flip and decisions (owner). Release cutting.

## Order and conflicts

P2-0 first; P2-1 to P2-3 modules touch separate files and merge in any order after it. M and G tasks are independent of P2. If a PR conflicts on `tests/census_pins.json`, rebase and repin, never hand-edit counts.

## Reporting

Each PR states gates run, census pins moved, and anything blocked. If a task does not finish in an hour, write what remains and stop.
