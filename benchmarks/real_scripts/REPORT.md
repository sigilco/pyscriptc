# Real script report

Generated 2026-11-09 by the measurement tool.

Command: `uv run python -m benchmarks.real_scripts.measure --markdown benchmarks/real_scripts/REPORT.md`.

Rung R0: 2 of 39 vendored third-party scripts lower with zero fences (5%). The corpus holds 294 fences in total.

The corpus is third-party source, vendored with a pinned commit and the upstream licence in `licenses/`. The manifest records the URL, the commit and the sha256 of every file. The scripts are not edited: they are measured as they are, so a fence names a real gap in the compiler and not a mistake in the fixture.

## Blockers, ranked

How many scripts each blocker touches, and how many it releases on its own. The score is the scripts released per day of work at the size the estimate gives. The rank is the work list.

| blocker | root cause | scripts touched | released alone | size | scripts/day |
| --- | --- | --- | --- | --- | --- |
| keyword-args | call-table | 2 | 2 | M | 1.0 |
| module-assign | statements | 2 | 1 | S | 1.0 |
| dynamic-annotation | inference | 1 | 1 | M | 0.5 |
| stdlib:os | stdlib-surface | 2 | 1 | M | 0.5 |
| construct-other | statements | 9 | 2 | L | 0.4 |
| annotation-other | inference | 3 | 0 | M | 0.0 |
| call-arity | call-table | 19 | 0 | S | 0.0 |
| field-lookup | classes | 6 | 0 | S | 0.0 |
| forward-reference | inference | 1 | 0 | S | 0.0 |
| generic-alias | inference | 1 | 0 | M | 0.0 |
| import-table | imports | 3 | 0 | M | 0.0 |
| local-inference | inference | 10 | 0 | S | 0.0 |
| name-resolution | inference | 21 | 0 | L | 0.0 |
| operator | operators | 2 | 0 | M | 0.0 |
| param-inference | inference | 13 | 0 | S | 0.0 |
| reflection | other | 1 | 0 | L | 0.0 |
| return-inference | inference | 3 | 0 | S | 0.0 |
| stdlib:dataclasses | stdlib-surface | 1 | 0 | M | 0.0 |
| stdlib:itertools | stdlib-surface | 1 | 0 | M | 0.0 |
| stdlib:pathlib | stdlib-surface | 1 | 0 | M | 0.0 |
| stdlib:random | stdlib-surface | 1 | 0 | M | 0.0 |
| stdlib:subprocess | stdlib-surface | 2 | 0 | M | 0.0 |
| stdlib:time | stdlib-surface | 1 | 0 | M | 0.0 |
| stdlib:zipfile | stdlib-surface | 1 | 0 | M | 0.0 |
| type-mismatch | checker | 9 | 0 | M | 0.0 |
| unlowered-call | call-table | 15 | 0 | L | 0.0 |
| unlowered-type-name | inference | 1 | 0 | M | 0.0 |

## Root causes, ranked

The families cascade: a name that is never declared fences as name-resolution on every later read, so the fence count ranks the symptom. A script is released by the root cause when every family it carries belongs to that cause. These are the epics, and the prize each one holds.

| root cause | scripts released |
| --- | --- |
| statements | 3 |
| call-table | 2 |
| inference | 2 |
| stdlib-surface | 1 |

## Stdlib modules, ranked

The stdlib module each fence names, and how many scripts it holds back. A module with a high demand count and a low blocked count is already first class; a module with a high demand count and a high blocked count is the next grind.

| module | scripts blocked |
| --- | --- |
| os | 2 |
| subprocess | 2 |
| dataclasses | 1 |
| itertools | 1 |
| pathlib | 1 |
| pylong | 1 |
| pyperf | 1 |
| random | 1 |
| sqlite3 | 1 |
| time | 1 |
| zipfile | 1 |

## Scripts

| script | compile | fences | top blocking features |
| --- | --- | --- | --- |
| batch_file_rename | fenced | 1 | construct-other (1) |
| black_schema | fenced | 1 | dynamic-annotation (1) |
| black_width_table | fenced | 1 | module-assign (1) |
| brute_force | fenced | 8 | param-inference (2), stdlib:time (2), call-arity (1) |
| cat | fenced | 9 | param-inference (3), unlowered-call (2), call-arity (1) |
| check_file | pass | 0 | - |
| checksum_script | fenced | 21 | name-resolution (13), unlowered-call (4), param-inference (3) |
| combinerefs | fenced | 6 | unlowered-call (3), local-inference (2), return-inference (1) |
| daily_checks | fenced | 9 | stdlib:subprocess (3), stdlib:os (2), operator (1) |
| dataclass_config | fenced | 6 | construct-other (3), forward-reference (1), reflection (1) |
| day_of_week | fenced | 5 | param-inference (2), call-arity (1), name-resolution (1) |
| divmod_threshold | fenced | 4 | name-resolution (2), import-table (1), unlowered-call (1) |
| duplicate_file_finder | fenced | 16 | name-resolution (6), type-mismatch (3), call-arity (2) |
| file_dispatcher | fenced | 1 | keyword-args (1) |
| file_ext_changer | fenced | 27 | construct-other (7), name-resolution (5), type-mismatch (5) |
| file_separator | fenced | 1 | keyword-args (1) |
| folder_size | fenced | 3 | local-inference (2), name-resolution (1) |
| folder_to_zip | fenced | 6 | call-arity (1), local-inference (1), name-resolution (1) |
| itertools_loader | fenced | 3 | annotation-other (1), construct-other (1), name-resolution (1) |
| job_scheduling | fenced | 23 | field-lookup (9), name-resolution (8), local-inference (4) |
| json_to_csv | fenced | 3 | annotation-other (1), call-arity (1), unlowered-call (1) |
| long_conv_tables | fenced | 3 | param-inference (2), unlowered-call (1) |
| move_files_over_x_days | fenced | 13 | name-resolution (9), local-inference (3), call-arity (1) |
| network_troubleshooter | fenced | 7 | field-lookup (2), name-resolution (2), type-mismatch (2) |
| organise | fenced | 6 | param-inference (2), unlowered-call (2), call-arity (1) |
| organise_by_extension | fenced | 5 | name-resolution (2), type-mismatch (2), call-arity (1) |
| ping_servers | fenced | 5 | name-resolution (3), type-mismatch (2) |
| powerup_checks | fenced | 19 | name-resolution (15), type-mismatch (2), call-arity (1) |
| puttylogs | fenced | 1 | stdlib:os (1) |
| random_file_move | fenced | 13 | name-resolution (7), local-inference (3), call-arity (1) |
| repo_info | fenced | 4 | return-inference (2), module-assign (1), name-resolution (1) |
| reveal_md | fenced | 10 | field-lookup (3), annotation-other (2), call-arity (2) |
| smart_file_organizer | fenced | 3 | construct-other (3) |
| snake_case_renamer | pass | 0 | - |
| sortperf | fenced | 31 | name-resolution (10), unlowered-type-name (10), call-arity (3) |
| ssh_host_script | fenced | 4 | param-inference (2), unlowered-call (2) |
| time_delta | fenced | 2 | call-arity (1), name-resolution (1) |
| webserver_log | fenced | 8 | name-resolution (5), param-inference (1), return-inference (1) |
| work_connect | fenced | 6 | stdlib:subprocess (4), call-arity (1), type-mismatch (1) |
