# Tree corpus report (R2)

Generated 2026-10-22 by the tree runner.

Command: `uv run python -m benchmarks.scripts_corpus.trees --markdown benchmarks/scripts_corpus/REPORT_R2.md`.

Rung R2 of the adoption ladder: 4 of 5 multi-module trees compile with zero fences (80%). The corpus holds 1 fences in total.

With binaries built and run against the CPython recordings: 4 match, 0 differ, 0 fail to build.

## Blocking features

How many trees each feature family holds back. The top row is the next rung for the compiler.

| family | trees blocked |
| --- | --- |
| stdlib:os | 1 |

## Trees

| tree | compile | fences | exec | detail | top blocking features |
| --- | --- | --- | --- | --- | --- |
| csv_join_pkg | pass | 0 | pass | - | - |
| env_layer | fenced | 1 | - | - | stdlib:os (1) |
| grep_ctx | pass | 0 | pass | - | - |
| log_stats | pass | 0 | pass | - | - |
| word_count_pkg | pass | 0 | pass | - | - |
