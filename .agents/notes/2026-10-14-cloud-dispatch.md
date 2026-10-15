# Cloud dispatch state (2026-10-14, refreshed ~22:10 CEST)

Repo: `sigilco/pyscriptc` (private). Master head: `2c605e7`. Full suite on master: 326 passed / 2 skipped / 0 failed in the Linux container; regression baseline log `/tmp/pyscriptc-master-baseline-20261014-145801.log`.

## Merged since last refresh

- #1 A5 verdict replay (4 replays match historical keep/drop)
- #3 A6 loop.md rewrite for v2
- #4 A2 fence triage (189 rows, 0 unclassified: 32 probe-wrong / 139 stricter / 18 compiler-gap / 1 out-of-scope)
- #5 S0 packaging (Nuitka onefile macOS arm64, smoke PASS)
- #6 relative `-o` cwd fix + regression test

## Open PRs

- #7 `work/a3-weights` -> master: weights match plan for corpus rows; blocked on A1 (weak-fold/raises/mention weight changes should land with the v2 JSONL).

## Orchestrator holds (cloud agents must NOT touch)

- Heavy lock `/tmp/pyscriptc-heavy.lock` and ALL container runs. gen4 (A1 reproducibility pair + 3.14) is running locally; A1 merges only after the a/b pair is byte-identical.
- Merges to master (PR #7 included), A7 `gates-v2` tag.

## In flight locally (not cloud tasks)

- A1 gen4 repro run in container 06fab1cb01aa; then rebase `work/a1-oracle312` onto master and merge.
- A4 timings + TIMINGS.md, then merge `work/a4-tier0`.

## Cloud task queue (pick in order, all off master unless noted)

- T9: full sharded calibration per plan row A2 (`.agents/plans/2026-10-14-waves-after-scorer-v2.md:55`). Write `benchmarks/compat2/probes/calibrate.py` shard driver; run OUTSIDE the container if the host has capacity (no heavy lock needed off-machine), >=10 sampled rows per category cell, land results in `probes/TRIAGE.md` tables. Report-only PR, do not touch `probes/*.jsonl`.
- T10: CI pipeline. GitHub Actions on 3.12 + 3.14, ubuntu + macos: ruff, pytest (unit only, no heavy marker), build smoke. Must pass on master `2c605e7` before enabling branch protection.
- T11: corpus README fix + census snapshot. Regenerate `benchmarks/compat2/census.py` output on the 47-function corpus, fix stale README claims, one PR.

## Ground rules for every cloud agent

- Conventional commits, no co-author trailers, no AI attribution.
- Tests must not regress vs the baseline list above (anything failing on master stays failing; new failures = your bug).
- Branch off latest master, one PR per task, report-only: no merges, no tags, no `probes/*.jsonl` writes, no container heavy runs.
- Terse docs, no emdashes, no AI-slop vocabulary ("leverage", "seamless", "robust", "delve").

## Stage 0 dispatched and merged (2026-10-15)

- #17 C1 cost baseline, #18 C3 entry-point spec, #19 C2 IR canonicity: all merged at master `5cb23cd`. Findings in `docs/e0-cost-baseline.md`, `docs/e1-entrypoint-detector-spec.md`, `docs/ir-canonicity.md`.
- C2 corrections: hash = SHA-256 canonical JSON; strip `span` recursive + `sourceFile`/`cpython`/`_notes`; sort classes, handler codes, fences by `(code, message, function or "", span.line, span.col)` before span strip; real set-order leak is `lower.py:607` synthetics (not llvm.py); `cache_key` at `benchmarks/compat2/cache.py:95` already hashes compiler files (Stage 2 item 2 is verify-only); scripts corpus is `corpus/scripts/*/main.py` (not `scripts/src/`).
- Briefs on /tmp: `/tmp/pyscriptc-c4-version-track-brief.md` (HOLD: Stage 4a only, post-freeze), `/tmp/pyscriptc-c5-release-followup-brief.md` (ready now, .github/ + packaging/ only).
