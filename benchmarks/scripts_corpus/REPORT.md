# Script corpus report

Generated 2026-10-22 by the corpus runner.

Command: `uv run python -m benchmarks.scripts_corpus.run --markdown benchmarks/scripts_corpus/REPORT.md`.

Rung R1 of the adoption ladder: 15 of 45 single-file CLI scripts compile with zero fences (33%). The corpus holds 116 fences in total.

With binaries built and run against the CPython recordings: 12 match, 3 differ, 0 fail to build. A script that compiles and still differs is a soundness signal, and it is tracked separately from a fence.

The 3 scripts that compile and still differ share one cause: `sys.exit(main())` evaluates `main()` twice, so the output is doubled. That is issue #183. Until it lands, the compiled pass rate is not the R1 rate: the ceiling is the compile rate above, and the true R1 rate is the compile rate minus the doubled scripts.

## Blocking features

How many scripts each feature family holds back. The top row is the next rung for the compiler.

| family | scripts blocked |
| --- | --- |
| name-resolution | 22 |
| construct-other | 13 |
| call-arity | 4 |
| keyword-args | 4 |
| local-inference | 4 |
| generic-alias | 3 |
| stdlib:sys | 3 |
| import-table | 1 |
| operator | 1 |
| stdlib:datetime | 1 |
| stdlib:os | 1 |

## Scripts

| script | compile | fences | exec | detail | top blocking features |
| --- | --- | --- | --- | --- | --- |
| access_summary | fenced | 9 | - | - | name-resolution (7), generic-alias (2) |
| argparse_convert | fenced | 10 | - | - | name-resolution (7), local-inference (2), keyword-args (1) |
| argparse_echo | fenced | 6 | - | - | name-resolution (5), keyword-args (1) |
| argparse_filter | fenced | 6 | - | - | name-resolution (5), keyword-args (1) |
| bar_chart | fenced | 1 | - | - | operator (1) |
| base64_codec | fenced | 2 | - | - | call-arity (2) |
| checksum_files | fenced | 5 | - | - | name-resolution (3), call-arity (2) |
| column_stats | pass | 0 | pass | - | - |
| copy_into | pass | 0 | pass | - | - |
| count_lines | pass | 0 | pass | - | - |
| csv_join | fenced | 3 | - | - | name-resolution (2), stdlib:sys (1) |
| csv_sort | fenced | 4 | - | - | name-resolution (2), call-arity (1), stdlib:sys (1) |
| csv_to_json | pass | 0 | pass | - | - |
| csv_totals | fenced | 1 | - | - | local-inference (1) |
| dedupe_lines | fenced | 4 | - | - | name-resolution (3), construct-other (1) |
| diff_lines | fenced | 1 | - | - | construct-other (1) |
| disk_usage | pass | 0 | wrong-output | stdout differs (217 bytes, CPython has 91) | - |
| env_report | fenced | 2 | - | - | name-resolution (1), stdlib:os (1) |
| find_files | pass | 0 | wrong-output | stdout differs (633 bytes, CPython has 192) | - |
| grep_lines | pass | 0 | pass | - | - |
| head_tail | fenced | 1 | - | - | construct-other (1) |
| ini_read | fenced | 7 | - | - | name-resolution (5), construct-other (2) |
| iso_dates | fenced | 9 | - | - | name-resolution (8), generic-alias (1) |
| json_flatten | pass | 0 | pass | - | - |
| json_pretty | pass | 0 | pass | - | - |
| json_select | fenced | 1 | - | - | construct-other (1) |
| json_to_csv | fenced | 4 | - | - | construct-other (2), name-resolution (1), stdlib:sys (1) |
| log_latest | fenced | 1 | - | - | construct-other (1) |
| merge_dict | fenced | 3 | - | - | name-resolution (2), construct-other (1) |
| number_format | pass | 0 | pass | - | - |
| old_files | fenced | 4 | - | - | name-resolution (2), keyword-args (1), stdlib:datetime (1) |
| reverse_dict | fenced | 4 | - | - | name-resolution (2), call-arity (1), construct-other (1) |
| running_median | pass | 0 | pass | - | - |
| shell_quote | pass | 0 | pass | - | - |
| slugify_names | fenced | 4 | - | - | name-resolution (3), generic-alias (1) |
| sort_lines | fenced | 1 | - | - | construct-other (1) |
| subprocess_run | fenced | 4 | - | - | name-resolution (3), local-inference (1) |
| table_render | fenced | 3 | - | - | name-resolution (2), construct-other (1) |
| tar_list | fenced | 2 | - | - | import-table (1), name-resolution (1) |
| template_fill | pass | 0 | pass | - | - |
| tree_print | pass | 0 | wrong-output | stdout differs (187 bytes, CPython has 124) | - |
| unique_lines | fenced | 5 | - | - | name-resolution (4), construct-other (1) |
| url_encode | pass | 0 | pass | - | - |
| url_split | fenced | 6 | - | - | name-resolution (5), local-inference (1) |
| word_count | fenced | 3 | - | - | name-resolution (2), construct-other (1) |
