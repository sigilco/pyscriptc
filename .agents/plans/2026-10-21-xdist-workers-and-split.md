# xdist worker bounds and pytest-split composition

Date: 2026-10-21. Scope: PR #128 follow-up. Machine: 8-core Apple Silicon Mac with 8 GB RAM, python 3.14, other apps resident (free plus inactive memory was 1.4 to 2.2 GB during runs, so numbers are noisy: a repeat of `-n 6` took 192s against 151s earlier).

## Measurements

CI selection is `tests --ignore=tests/compat2 -m "not linux_container"`: 534 passed, 14 skipped (not 600; the 600 figure included compat2). Wall time and peak summed RSS of the whole process tree (pytest, workers, clang, zig, built binaries; sampled every 0.5s):

| workers | wall | user CPU | tree peak RSS |
|---|---|---|---|
| 2 | 251s | 302s | 309 MB |
| 4 | 177s | 381s | 595 MB |
| 6 | 151s | 418s | 558 MB |
| 8 | 155s | 477s | 821 MB |

The knee is at 6. Going to 8 adds 14% user CPU and 50% memory for no wall gain, which is contention (sys time also climbs). Per worker, the tree costs about 0.1 GB; the largest single process seen was 0.2 GB (maximum resident set size, 180 to 214 MB across runs). Four slow `*_matches_cpython` tests run serially peaked at 207 MB for the whole tree. No test needs more than a few hundred MB, so CPU, not memory, is the binding limit on any runner. Memory only matters on a loaded 8 GB laptop.

The slowest single test in this selection is 22.6s (`test_r33_raises`), not 165s; `test_numeric_tower_stays_fenced` is 1.4s. The 165s figure does not appear in `tests/.test_durations`. Sum of recorded durations for the selection: 721s.

## Runner specs (GitHub docs, repo sigilco/pyscriptc is public, org plan is free)

| label | vCPU | RAM |
|---|---|---|
| macos-14 (M1) | 3 | 7 GB |
| ubuntu-24.04 / ubuntu-latest, public repo | 4 | 16 GB |

Larger runners need a Team or Enterprise Cloud plan; the org is on the free plan and the repo has no self-hosted runners, so they are not available. Containers: `os.cpu_count()` reports host cores while cgroup v2 `cpu.max` and `memory.max` carry the real limit, so the helper reads those. `os.process_cpu_count()` (3.13+) honours affinity; `sched_getaffinity` does not exist on macOS.

## Helper

`scripts/pytest_workers.py` computes `workers = clamp(min(cpus, floor((mem_gb - RESERVE_GB) / PER_WORKER_GB)), 1, CAP)` with `PER_WORKER_GB=0.15`, `RESERVE_GB=0.25`, `CAP=6`. Memory is Linux `MemAvailable` and cgroup `memory.max - memory.current`, or on macOS `vm_stat` free + inactive + speculative + purgeable. `PYTEST_WORKERS=N` overrides; `--explain` prints inputs.

A root `conftest.py` implements `pytest_xdist_auto_num_workers`, so `-n auto` uses the bound. That beats a script-only approach because CI and docs keep saying `-n auto` and nothing in the workflow changes; the script form (`-n $(python scripts/pytest_workers.py)`) still works. Defaults: 6 on this Mac when memory allows, 3 on macos-14, 4 on public ubuntu-24.04. Thresholds are deliberately loose because inactive pages are reclaimable: first try (0.2 GB per worker, 0.5 reserve) chose 4 here and ran 190s versus 151s, so it was too strict.

Verification: with the final thresholds the CI selection under `-n auto` chose 6 (mem 2.2 GB) and passed 534 tests in 192s on a busy host; the clean `-n 6` figure above is the reference.

## Composition with pytest-split

Each shard is its own runner job, so workers per shard equal that runner's cores (3 on macos-14) and shards never share a machine; N shards times W workers does not oversubscribe. Shards help only until the longest test dominates. Critical path estimate is `max(total / (N * W), longest)` with total 721s, W=3, longest 22.6s: N=1 240s, N=2 120s, N=3 80s, N=4 60s, N=6 40s, N=8 30s. Each shard also pays about a minute of checkout, uv sync and cache restore, so returns fall off past 3 or 4. Recommendation: keep 3 shards on macos-14 (about 80s of tests); go to 4 only if shard setup is cheaper than the 20s it saves, which it is not. The test floor is the 22.6s test, reached near N=8, never worth it. Refresh durations with `uv run pytest -n 6 -m "not linux_container" --store-durations --durations-path tests/.test_durations`.
