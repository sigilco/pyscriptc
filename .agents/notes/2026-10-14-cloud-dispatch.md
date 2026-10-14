# Cloud dispatch state (2026-10-14 21:00 CEST)

Repo: `sigilco/pyscriptc` (private). Master head: `966058c` (A0 merged). Full suite on master: 326 passed / 2 skipped / 0 failed in the Linux container, baseline log `/tmp/pyscriptc-master-baseline-20261014-145801.log`.

## Branches on origin (all pushed, current)

| Branch | Head | Content | Status |
| --- | --- | --- | --- |
| `master` | `966058c` | A0 macOS crash fix merged | green in container |
| `work/a1-oracle312` | `c812412` | oracle 3.12 retarget + 4 reproducibility fixes | generate pair running locally; merge after it passes |
| `work/a4-tier0` | `72ffc03` | Tier 0, cache, bundles, shard merge + mention/rlimit fixes | timings not measured yet; TIMINGS.md missing |
| `work/a5-verdict` | `e6d3cca` | verdict replay harness + macOS baseline plumbing | PR #1 |
| `work/a6-loop` | `49b1a7e` | loop.md rewrite for v2 | PR #3 |
| `work/a2-triage` | `752a053` | 189 v1 fence rows triaged, 0 unclassified | PR #4 |
| `work/s0-packaging` | `e24da97` | divergent Cursor-agent packaging commit (DO NOT USE) | superseded |
| `work/s0-packaging-2` | `7233aed` | real packaging: Nuitka onefile macOS arm64, smoke PASS | PR #2 points at the WRONG branch head |

## Draft PRs

- #1 a5-verdict -> master
- #2 packaging -> master (head ref must be repointed to `work/s0-packaging-2`)
- #3 a6-loop -> master
- #4 a2-triage -> master

## Orchestrator holds (do not let cloud agents touch)

- The heavy lock (`/tmp/pyscriptc-heavy.lock`) and all container runs: gen4 reproducibility pair for A1 is running now.
- `benchmarks/compat2/probes/*.jsonl` regeneration.
- Merges to master; A2 full sharded calibration; A3 weight change; A7 `gates-v2` tag.

## Known contradictions (verified by subagents, not yet fixed)

1. `outcomes.py` weights vs plan: `program` 5.0 (plan says scripts 3.0), function 1.0 (plan 2.0), weak folds 0.25 and raises-weight-0 absent (A3 scope).
2. `cli.py:118` runs the built program via bare `-o prog.bin` which resolves through PATH, not cwd (packaging smoke works around it with an absolute path).
3. `fold.py:53` uses PEP 758 `except A, B:` syntax, valid only on 3.14; the file claims 3.10 parseability is required for workers.
4. Master MANIFEST still says oracle 3.14 until A1 merges.

## Safe cloud tasks (no heavy lock, no file overlap with the orchestrator)

- T5: review PRs #1 to #4 against their plan rows (report-only).
- T6: draft A3 weights diff on a new `work/a3-weights` branch off `work/a1-oracle312` (do not merge).
- T7: draft the `cli.py` relative-`-o` fix on `work/fix-cli-outpath` off master (do not merge).
- T8: repoint PR #2 head to `work/s0-packaging-2` (or close and reopen against it).
