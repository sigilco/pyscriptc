# Benchmarks audit (2026-10-22)

Question: why are there multiple benchmark directories, is there any use to it?

Short answer: they measure different things and mostly have different consumers. `compat`/`compat2` measure language coverage (frozen gates, the scorer); `baseline`/`runtime`/`scaling`/`native` measure performance and are referenced from the README; `adoption` and `builds` are documentation-only placeholders. There is real overlap only between `baseline` and `runtime`, and `builds` is an empty placeholder. Nothing should be deleted now; `compat` is the candidate to archive once nothing reads it.

## Inventory

| Directory | Size | Consumers | Last meaningful change | Frozen history? | Verdict |
| --- | --- | --- | --- | --- | --- |
| `benchmarks/adoption` | 8 KB, 1 file (README only) | README (adoption story), `.agents/docs/map.md` | 2026-10-14 snapshot import | Effectively: prose, no code | KEEP — cheap, documents why outside suites are not vendored; could fold into docs later |
| `benchmarks/baseline` | 84 KB, 9 files | `tests/test_baseline.py`, `tests/compat2/test_verdict_replay.py`, CI check `uv run python -m benchmarks.baseline.run --check`, README perf numbers, AGENTS.md | 2026-10-15 re-recorded Linux reference | No (reference.json re-recorded) | KEEP — it is the perf regression gate; overlaps with `runtime` workloads but stores the reference ratios |
| `benchmarks/builds` | 4 KB, 1 file (README only) | Nothing: zero references outside benchmarks/ | 2026-10-14 snapshot import | Yes: placeholder for multi-module programs that do not lower under gates-v1 | ARCHIVE — self-declared placeholder; either fold the note into `benchmarks/README.md` or delete at a minor bump |
| `benchmarks/compat` | 22 MB, ~1550 files | v1 scorer, `tests/test_compat.py` and many compat tests, README links | 2026-10-17 PyJWT census | Yes: v1 is frozen history per AGENTS.md; gates moved to v2 | MERGE/ARCHIVE candidate — biggest dir, frozen; keep until confirmed no test or tooling still reads it, then archive out of the tree (it is 22 MB of history) |
| `benchmarks/compat2` | 28 MB, ~2070 files | The live scorer: nightly workflow (score, boxed_cost), ir-hashes CI, dozens of tests, frozen gate files, boxed ledger | Active (2026-10-22) | Gate files frozen (`gates-v2`); corpus and probes still grow | KEEP — this is the actual gate |
| `benchmarks/native` | 52 KB, 13 files | `tests/test_nativeprobe.py`, `python -m benchmarks.native.run` (manual), docs/architecture.md | 2026-10-14 snapshot import | Partially: code lives, rarely run | KEEP — only coverage of the C-ABI/.so path; small |
| `benchmarks/runtime` | 384 KB, 91 files | `tests/test_benchmarks.py`, CI ruff excludes, README perf numbers, `python -m benchmarks.runtime.run` | 2026-10-18 R26 workload | No | KEEP — per-module perf kernels; could merge into `baseline` (they share the suite shape) but that is churn for no gain |
| `benchmarks/scaling` | 20 KB, 4 files | `tests/test_benchmarks.py`, README (`python -m benchmarks.scaling.run`) | 2026-10-14 snapshot import | Partially: original loops only | KEEP — small, mirrors scriptc layout; candidate to merge into `runtime` if the owner wants fewer dirs |
| `benchmarks/scripts_corpus` | 868 KB, 210 files | `tests/test_scripts_corpus.py`, compat2 corpus loader | Active (2026-10-22) | No: R1 adoption-ladder corpus, still growing | KEEP — newest corpus, feeds compat2 |
| `benchmarks/README.md` | 2 KB | Documentation table mapping dirs to what runs | 2026-10-14 | n/a | KEEP — update if anything is archived |
| `benchmarks/factsdiff.py` | 13 KB | `docs/external-oracles.md`, CHANGELOG, run manually as `-m benchmarks.factsdiff` | 2026-10-21 pyrefly-tsp report | No | KEEP — facts-oracle sanity tool, documented |
| `benchmarks/harness.py` | 6 KB | Shared runner helpers imported by `baseline/run.py`, `native/run.py`, `runtime/run.py`, `scaling/run.py` | 2026-10-14 (helpers), 2026-10-21 (`factsdiff.py`) | n/a | KEEP — the shared runner core |

## Reasons

- The split is inherited from scriptc's `benchmarks/` layout (`runtime`, `scaling`, `builds` mirror it) plus pyscriptc-specific additions (`compat`, `compat2`, `baseline`, `adoption`, `native`, `scripts_corpus`).
- Coverage benchmarks (compat, compat2, scripts_corpus) and performance benchmarks (baseline, runtime, scaling, native) answer different questions and are gated separately; merging them would conflate the frozen scorer gates with the perf harness.
- Real waste: `builds` (empty placeholder, no consumers) and `compat` (22 MB frozen v1 that v2 replaced). Both are archive candidates, not deletions, because AGENTS.md freezes v1 scorer history.
- Consolidation opportunity, low priority: `runtime`, `scaling` and `baseline` share the suite/runner shape and could become one `perf/` package. Not recommended now: it would touch tests and CI for no measurement gain.
