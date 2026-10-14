# Wave A handoff (2026-10-14)

Plan: `.agents/plans/2026-10-14-waves-after-scorer-v2.md` (adopted in commit 93a5faf). Master head at handoff: 93a5faf. The sessions were halted on request, so everything below is a snapshot, not a finished wave.

## Baseline (rule 9c)

`/tmp/pyscriptc-master-baseline-*.log`: 326 passed, 2 skipped, 0 failed, 17m20s on master c6ad189 (the plan commit adds no code). No known pre-existing failures. `/tmp` may be cleaned, so rerun `HEAVY=1 .agents/docker/run.sh uv run pytest -q` once if the log is gone. Delete it when Wave A lands.

## Status per plan row

| Id | State | Branch and worktree |
| --- | --- | --- |
| A0 macOS crash | Done, not merged. Commits cf3f0e7 and 0a3b3db. `pyscriptc compile examples/add.py` works on macOS arm64. `examples/hello.py` does not exist, so `add.py` stands in for the exit criterion. Full suite not yet run on this branch | `work/a0-macos`, `~/.worktrees/pyscriptc-a0-macos` |
| A1 oracle 3.12 | Mostly implemented, unverified. 9 commits: docker interpreters 3.10 to 3.14, typeshed `VERSIONS` filter, cpython_tests re-pin to v3.12.15 with v3.14.8 kept, language profile per oracle ast, `versions` field, per interpreter runner, `--oracle` flag, kill-pid fix. Uncommitted edits in `probes/README.md` and `tests/test_compat2_probes.py` (kept in the worktree). Missing: the byte for byte reproducibility check (the two 3.12 generate runs and the 3.14 run were killed, none finished; the first ran 10 min before it was killed), regenerated JSONL and MANIFEST committed, per category 3.12 vs 3.14 row counts | `work/a1-oracle312`, `~/.worktrees/pyscriptc-a1-oracle312` |
| A2 | Not started (needs A1) | |
| A3 | Not started (needs A2) | |
| A4 Tier 0 and caching | Partly done. 6 commits: `cache.py` with oracle version in keys, narrowed cache keys from the compiler file layout, `coverage_map.py` with Tier 0 selection by git diff, 50 probe bundles, automatic shard merge, `xfail` on RSS 0 in `test_baseline`. Missing: any measurement. `TIMINGS.md` does not exist, so the Tier 0 under 30 s and Tier 1 under 3 min targets are unverified. Scratch measuring scripts are in the worktree's `.scratch/` (untracked, not committed) | `work/a4-tier0`, `~/.worktrees/pyscriptc-a4-tier0` |
| A5 to A7 | Not started | |
| Phase 9 S0 packaging | Early. Untracked `packaging/` (build.sh, entry.py, TOOLCHAIN.md, README.md, smoke.sh, smoke/, plus build/ and dist/ output dirs) and an uncommitted Nuitka optional dependency in `pyproject.toml` and `uv.lock`. Nothing committed, no build result known | `work/s0-packaging`, `~/.worktrees/pyscriptc-s0-packaging` |

## Next steps in order

1. Merge `work/a0-macos` into master (fast, 2 commits), run the full suite against the baseline.
2. Finish A1: commit the pending README and test edits, rerun the two 3.12 generates (`--out` to different dirs, diff them) and one 3.14, one at a time through `HEAVY=1`, then commit regenerated JSONL and MANIFEST. Each generate took over 10 minutes.
3. In parallel with A1, finish A4: run the measurements, write `benchmarks/compat2/TIMINGS.md`, then merge. A4 and A1 touch disjoint files, but rebase A4 after A1 since both read the oracle version.
4. Then A2 (full sharded calibration, triage table `probes/TRIAGE.md`, stop condition of 3 days), A3 (weights), A5 (replay verdict, baselines), A6 (`loop.md`), A7 (tag `gates-v2`). Phase 9 S0 can resume any time, it only adds `packaging/`.
5. Wave B starts only after the `gates-v2` tag.

## Things to remember

- Docker: heavy runs share a lock at `/tmp/pyscriptc-heavy.lock` and the Dory VM is small (2 GB). Run one heavy job at a time. At handoff the lock was cleared and no containers were running.
- Orphans: stopping an agent does not stop the shell scripts or containers it started (A1 had a driver script at `/tmp/a1-run.sh`). After halting agents, check `docker ps` and `pgrep -fl run.sh`.
- Commits carry no co-author trailer (global rule), despite the harness reminder that asks for one.
- `.agents/plans/` is gitignored. Files there are force added (`git add -f`).
- `stdmore.py` has existing `ruff format` diffs outside the A0 changes. Leave them unless a task owns that file.
- Decision capture (`_own/.agents/decisions.md` row and the content question) is due at the end of each wave, not yet done for Wave A.
