# Cloud agent handoff 3: stdlib surface, bug fixes, bench and CI lanes

Date: 2026-11-05. Epic for the compiler program: #817. This batch is the independent product backlog; it does not touch the compiler re-architecture files. Batches 1 and 2 (`2026-11-05-cloud-agent-handoff.md`, `-2.md`) own the emit seam, MIR, A/B harness, coverage metric, self-host fences and typed-HIR passes; local sessions own A1, C1 to C4 and B3/B4.

## Rules for every task

- Read `AGENTS.md` first; obey its guard rules. One branch and one PR per issue, base `master`.
- Planning lives in GitHub: these issues sit in milestone `v0.10.0`. The PR carries the same milestone and `Closes #N`. Do not change milestones, issue bodies or the board.
- Documentation policy: user docs state divergences from CPython and known gaps, never feature lists. When support changes, update the affected divergence or gap entry in the same PR. Support state lives on the compat site data.
- Master is PR only. Never tag or release. No co-author trailers. Conventional atomic commits, one commit per verified unit.
- Do not edit frozen gate files (`benchmarks/compat2/outcomes.py`, `score.py`, `coverage_map.py`, `cache.py`, `calibrate.py`, `CALIBRATION.json`, `TRIAGE.md`, `probes/*.jsonl`, `probes/IR_HASHES.json`, `probes/TIR_HASHES.json`, `canonical.py` rules). If a change would alter an IR or TIR hash or needs a probe edit, stop and write the blocker in the PR.
- Never read or ask for any closed corpus split; work from the visible corpus only.
- New stdlib or construct support lands with a differential probe against CPython 3.14 (the target, see `docs/reference/divergences.md`). Do not assume 3.12 or the host interpreter.
- Gates before every commit: `uv run ruff check`, `uv run ruff format --check`, the tests the task names, and `tests/test_census_self.py`. Run the CI selection with `uv run pytest -n auto -m "not linux_container" --ignore=tests/compat2`. Repin census pins only when a count moves, with the repin script.
- No absolute home paths in repo notes.

## First step for every task

Read the issue and check master: if the work is already done or the issue is stale, write one line in the PR description of a docs-free no-op PR or comment on the issue, and move to the next task instead of inventing work.

## Group S: stdlib surface (runtime C and `restlower/`; conflicts only inside the same stdlib module)

- S-a #509 `os.walk`, `json.load` and `time.perf_counter` (the collector surface). One PR per function if they touch different files.
- S-b #485 a closed `selectors` subset over the `ps_loop` event loop.
- S-c #877 pick the two smallest of flock, symlink, mkstemp, chown from the native ops-script surface; leave `Popen` and `tarfile` for later.
- S-d #502 import rows only: `platform` and `calendar`. Skip `zipfile` and `tarfile` here.

## Group B: bug fixes (each is one file area)

- B-a #764 a non-identifier module file must not refuse the whole tree scan.
- B-b #765 a program island must not keep the binary path in `argv[0]` (breaks argparse prog).
- B-c #484 the coverage tool must not crash on a dependency tree; add a regression test with a small fixture tree.
- B-d #730 assess and coverage survive a real package tree (read after B-c, same area; do it second and rebase).

## Group L: bench and CI lanes (scripts and workflows, no compiler code)

- L-a #526 the verdict ratchet tracks a known wrong-output row instead of freezing it.
- L-b #527 an unstripped-byte corpus comparison lane beside the stripped one.
- L-c #506 a nightly lane that tracks the real-scripts corpus pass rate and publishes it on the ladder.
- L-d #523 a differential probe lane in the nightly that byte-compares against CPython. Check #924 and the 2026-11-05 nightly state before starting; if the nightly is red for an unrelated cause, say so instead of changing thresholds.

## Group E: embed docs (docs only)

- E-a #671 docs for the engine contract and what is not our responsibility. State what fails or is out of scope, not a feature list.

## Explicitly out of scope

Anything under `src/pyscriptc/lower/` (class constants #443, property setters #439, generators #437 to #442, protocol dispatch #445 and #446): the lowering modules are being split and fenced by other work. Anything in `emit/`, `mir*.py`, `_irnode.py`, `canonical.py`. Release cutting. GitHub housekeeping.

## Order and conflicts

Groups S, B, L and E touch different areas and merge in any order. Within a group, tasks touching the same file go sequentially (rebase on master first). If two PRs conflict on `tests/census_pins.json` or compat site data, rebase and regenerate, never hand-edit counts.

## Reporting

Each PR states: the issue closed, gates run, census pins moved, probes added, and anything blocked. If a task does not finish inside an hour, write what remains and stop.
