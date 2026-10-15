# compat2 tier timings (A4)

Measured on the rebased `work/a4-tier0` branch, 2026-10-15.

## Host

- Linux x86_64 (Ubuntu 22.04), 8 cores, 31 GB RAM — a bare Linux box, not the Dory container.
- clang 15.0.7 (Ubuntu `clang-15` package; clang 14 cannot assemble the emitted IR — it lacks
  default opaque-pointer support and rejects `%ps.str = type { i64, ptr }`).
- CPython 3.15.0 (uv-managed), `uv run`.

## Method

All runs use the full probe set (`benchmarks/compat2/probes`, 14,285 probes) with
`--workers 8`. Wall time is the `time` builtin around the `uv run` command; the scorer's own
`scored N probes in Xs` line is the in-process scoring time.

1. Coverage map: `python -m benchmarks.compat2.coverage_map build benchmarks/compat2/probes`
   (one instrumented pass over every probe; cached afterwards).
2. Tier 1 cold: `score benchmarks/compat2/probes --out tier1.json` with the result cache
   emptied (`benchmarks/compat2/.cache` minus `coverage-map.json`).
3. Tier 1 warm: identical command, immediately after the cold run.
4. Tier 0: `score benchmarks/compat2/probes --tier 0 --base 24cf5f5`, warm cache.
   `24cf5f5` predates a change to `llvm.py`, so the diff hits a shared core file and
   selection falls back to the fixed-seed stratified sample of 300 — the worst case for
   Tier 0 selection size.

## Results

| Run | Selection | Wall time | Target | Verdict |
| --- | --- | --- | --- | --- |
| Coverage map build | 14,285 probes | 12.9 s | — | one-time cost, cached |
| Tier 1 cold (empty cache) | 14,285 probes | 70.7 s (69.8 s scored) | — | context only |
| Tier 1 warm | 14,285 probes | 0.75 s | < 3 min | pass |
| Tier 0 (shared-core diff) | 300 probes (stratified) | 0.24 s | < 30 s | pass |

Tier 0 against a diff that touches only leaf compiler files is faster still: intersection
selects fewer probes and each is a cache hit. The warm numbers are dominated by JSONL
loading and cache-key hashing, not compilation.

## Caveats

- The cold Tier 1 number moves with `--workers` and probe mix; on this box ~78% of probes
  fence before codegen, so clang time is not the dominant term.
- Timing runs of this suite previously corrupted the worktree: probes such as
  `stdlib.pathlib.Path.chmod` (`Path().chmod(3)`) ran with the repo root as cwd and chmod'd
  it to 003, and file-writing probes littered it. Probe code now runs with an isolated
  temporary cwd (oracle, batch oracle, compiled binaries, and `--run` programs).
