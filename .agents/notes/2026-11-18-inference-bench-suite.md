# The compile-time inference benchmark suite (issue #1319)

Date: 2026-11-18. Branch: `work/imp1319` from `origin/master` at `059cf180`. Milestone: v0.11.0. Design note: `.agents/docs/design/inference-perf-baseline.md` (stage 1 of the staged rollout). This is stage 1 only: the suite and the report-only gate. The skip fast path (stage 0) landed separately as issue #1317, and the per-module fact cache (stage 2, option P1) is still open.

## What landed

`benchmarks/baseline/inference.py` runs three in-repo trees twice each, once plain and once with `--infer`, and records the wall-clock split plus the engine's own report (pins, call sites, skipped flag) from the compile's `--debug-dir` summary:

| workload | tree | what it prices |
| --- | --- | --- |
| `wordcount-infer` | `examples/wordcount` | the skip fast path (#1317): a fully annotated tree must show the engine skipped |
| `real-scripts-infer` | `benchmarks/real_scripts/scripts` (39 modules) | cold-cache inference on a real mixed-annotation tree |
| `synthetic-150-infer` | generated ~150-module chain (`benchmarks/baseline/synthetic.py`) | the tree size the design note measured `src/pyscriptc` at, hermetic and in-repo |

The runner is `uv run python -m benchmarks.baseline.run --suite inference`. The suite refuses `--record` and `--check`, so no reference file exists and no ratio gate can read the rows: `infer_ms` is a per-release receipt, and the gate arms after two releases of data, matching the `bench.yml` discipline.

`benchmarks/baseline/delta.py --inference` prints the rows of one run or the change in `infer_ms` per workload between two runs. `.github/workflows/bench.yml` runs the suite next to the runtime suite (`continue-on-error`, report-only), writes `infer-<tag-or-sha>.json`, and prints the delta against the previous tag when that asset exists.

## Measured on this host

CPython 3.14.9, Linux x86_64 VPS, other sessions active, so the absolute numbers are load-noisy; the ratios are stable.

| workload | compile ms | infer ms | overhead | engine ms | pins | call sites |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `wordcount-infer` | 480 | 6035 | 12.6x | 1 (skipped) | 0 | 0 |
| `real-scripts-infer` | 1446 | 23868 | 16.5x | 16523 | 29 | 128 |
| `synthetic-150-infer` | 1354 | 23135 | 17.1x | 16440 | 151 | 152 |

Two readings matter more than the absolute numbers. First, the skip fast path removes the engine's work on the annotated tree (the engine's own row is 1 ms, the AST walk), but not the process cost: the `--infer` compile still pays about 5.5 s of checker bridge and startup, so the compile-time ratio stays near 13x until the default flips. The suite therefore records the engine's own wall time next to the compile's, so the two are never confused. Second, the synthetic chain of 150 modules costs about as much as the 39-module real tree: the cost is dominated by the checker's fixed start-up plus the mirror pass, not by the module count alone. A bigger synthetic tree would not add signal yet.

## Notes for the next session

The suite runs `--skip-check` on purpose: the rows price inference, not the checker preflight, and the preflight's own failures on the real-scripts tree (170 PS2002 fences) must not turn into error rows. The real-scripts tree fences, and a fenced compile is still a measured row: the fences belong to the tree, not to the engine. Only a compile that fails before writing its summary is an error row.

The `bench.yml` job runs the inference suite with `continue-on-error: true` and the artifact upload with `if-no-files-found: ignore`, so a missing inference run is a note in the summary and never a failed workflow.
