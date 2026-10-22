# Script corpus report

Generated 2026-10-21 by the corpus runner.

Command: `uv run python -m benchmarks.scripts_corpus.run --markdown benchmarks/scripts_corpus/REPORT.md`.

Rung R1 of the adoption ladder: 13 of 45 single-file CLI scripts compile with zero fences (28%). The corpus holds 124 fences in total.

With binaries built and run against the CPython recordings: 0 match, 13 differ, 0 fail to build. A script that compiles and still differs is a soundness signal, and it is tracked separately from a fence.

The 13 scripts that compile and still differ share one cause: `sys.exit(main())` evaluates `main()` twice, so the output is doubled. That is issue #183. Until it lands, the compiled pass rate is not the R1 rate: the ceiling is the compile rate above, and the true R1 rate is the compile rate minus the doubled scripts.

## Blocking features

How many scripts each feature family holds back. The top row is the next rung for the compiler.

| family | scripts blocked |
| --- | --- |
| name-resolution | 22 |
| construct-other | 13 |
| call-arity | 4 |
| import-table | 4 |
| local-inference | 4 |
| generic-alias | 3 |
| keyword-args | 3 |
| stdlib:sys | 3 |
| stdlib:datetime | 2 |
| operator | 1 |
| stdlib:os | 1 |

## Scripts

| script | compile | fences | exec | detail | top blocking features |
| --- | --- | --- | --- | --- | --- |
| access_summary | fenced | 9 | - | - | name-resolution (7), generic-alias (2) |
| argparse_convert | fenced | 11 | - | - | name-resolution (8), local-inference (2), import-table (1) |
| argparse_echo | fenced | 7 | - | - | name-resolution (6), import-table (1) |
| argparse_filter | fenced | 7 | - | - | name-resolution (6), import-table (1) |
| bar_chart | fenced | 1 | - | - | operator (1) |
| base64_codec | fenced | 2 | - | - | call-arity (2) |
| checksum_files | fenced | 5 | - | - | name-resolution (3), call-arity (2) |
| column_stats | pass | 0 | wrong-output | stdout differs (270 bytes, CPython has 135) | - |
| copy_into | pass | 0 | wrong-output | stdout differs (60 bytes, CPython has 30) | - |
| count_lines | pass | 0 | wrong-output | stdout differs (50 bytes, CPython has 25) | - |
| csv_join | fenced | 3 | - | - | name-resolution (2), stdlib:sys (1) |
| csv_sort | fenced | 4 | - | - | name-resolution (2), call-arity (1), stdlib:sys (1) |
| csv_to_json | fenced | 1 | - | - | keyword-args (1) |
| csv_totals | fenced | 1 | - | - | local-inference (1) |
| dedupe_lines | fenced | 4 | - | - | name-resolution (3), construct-other (1) |
| diff_lines | fenced | 1 | - | - | construct-other (1) |
| disk_usage | pass | 0 | wrong-output | stdout differs (470 bytes, CPython has 235) | - |
| env_report | fenced | 2 | - | - | name-resolution (1), stdlib:os (1) |
| find_files | pass | 0 | wrong-output | stdout differs (1392 bytes, CPython has 696) | - |
| grep_lines | pass | 0 | wrong-output | stdout differs (20 bytes, CPython has 10) | - |
| head_tail | fenced | 1 | - | - | construct-other (1) |
| ini_read | fenced | 7 | - | - | name-resolution (5), construct-other (2) |
| iso_dates | fenced | 13 | - | - | name-resolution (8), local-inference (2), stdlib:datetime (2) |
| json_flatten | pass | 0 | wrong-output | stdout differs (66 bytes, CPython has 33) | - |
| json_pretty | fenced | 1 | - | - | keyword-args (1) |
| json_select | fenced | 1 | - | - | construct-other (1) |
| json_to_csv | fenced | 4 | - | - | construct-other (2), name-resolution (1), stdlib:sys (1) |
| log_latest | fenced | 1 | - | - | construct-other (1) |
| merge_dict | fenced | 3 | - | - | name-resolution (2), construct-other (1) |
| number_format | pass | 0 | wrong-output | stdout differs (170 bytes, CPython has 85) | - |
| old_files | fenced | 3 | - | - | name-resolution (2), stdlib:datetime (1) |
| reverse_dict | fenced | 4 | - | - | name-resolution (2), call-arity (1), construct-other (1) |
| running_median | pass | 0 | wrong-output | stdout differs (160 bytes, CPython has 80) | - |
| shell_quote | pass | 0 | wrong-output | stdout differs (40 bytes, CPython has 20) | - |
| slugify_names | fenced | 4 | - | - | name-resolution (3), generic-alias (1) |
| sort_lines | fenced | 1 | - | - | construct-other (1) |
| subprocess_run | fenced | 4 | - | - | name-resolution (3), keyword-args (1) |
| table_render | fenced | 3 | - | - | name-resolution (2), construct-other (1) |
| tar_list | fenced | 2 | - | - | import-table (1), name-resolution (1) |
| template_fill | pass | 0 | wrong-output | stdout differs (74 bytes, CPython has 37) | - |
| tree_print | pass | 0 | wrong-output | stdout differs (392 bytes, CPython has 196) | - |
| unique_lines | fenced | 5 | - | - | name-resolution (4), construct-other (1) |
| url_encode | pass | 0 | wrong-output | stdout differs (20 bytes, CPython has 10) | - |
| url_split | fenced | 6 | - | - | name-resolution (5), local-inference (1) |
| word_count | fenced | 3 | - | - | name-resolution (2), construct-other (1) |
