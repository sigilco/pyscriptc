# Real script report

Generated 2026-10-24 by the measurement tool.

Command: `uv run python -m benchmarks.real_scripts.measure --markdown benchmarks/real_scripts/REPORT.md`.

Rung R0: 0 of 39 vendored third-party scripts lower with zero fences (0%). The corpus holds 386 fences in total.

The corpus is third-party source, vendored with a pinned commit and the upstream licence in `licenses/`. The manifest records the URL, the commit and the sha256 of every file. The scripts are not edited: they are measured as they are, so a fence names a real gap in the compiler and not a mistake in the fixture.

## Blockers, ranked

How many scripts each blocker touches, and how many it releases on its own. The score is the scripts released per day of work at the size the estimate gives. The rank is the work list.

| blocker | root cause | scripts touched | released alone | size | scripts/day |
| --- | --- | --- | --- | --- | --- |
| module-assign | statements | 2 | 1 | S | 1.0 |
| dynamic-annotation | inference | 1 | 1 | M | 0.5 |
| annotation-other | inference | 1 | 0 | M | 0.0 |
| call-arity | call-table | 11 | 0 | S | 0.0 |
| construct-other | statements | 6 | 0 | L | 0.0 |
| field-lookup | classes | 2 | 0 | S | 0.0 |
| forward-reference | inference | 1 | 0 | S | 0.0 |
| generic-alias | inference | 1 | 0 | M | 0.0 |
| import-table | imports | 6 | 0 | M | 0.0 |
| keyword-args | call-table | 8 | 0 | M | 0.0 |
| local-inference | inference | 19 | 0 | S | 0.0 |
| name-resolution | inference | 22 | 0 | L | 0.0 |
| operator | operators | 1 | 0 | M | 0.0 |
| param-inference | inference | 20 | 0 | S | 0.0 |
| parameter-forms | classes | 1 | 0 | M | 0.0 |
| return-inference | inference | 17 | 0 | S | 0.0 |
| stdlib:dataclasses | stdlib-surface | 1 | 0 | M | 0.0 |
| stdlib:itertools | stdlib-surface | 1 | 0 | M | 0.0 |
| stdlib:json | stdlib-surface | 2 | 0 | M | 0.0 |
| stdlib:os | stdlib-surface | 3 | 0 | M | 0.0 |
| stdlib:random | stdlib-surface | 1 | 0 | M | 0.0 |
| stdlib:subprocess | stdlib-surface | 1 | 0 | M | 0.0 |
| stdlib:time | stdlib-surface | 3 | 0 | M | 0.0 |
| type-mismatch | checker | 2 | 0 | M | 0.0 |
| unlowered-call | call-table | 21 | 0 | L | 0.0 |
| unlowered-type-name | inference | 1 | 0 | M | 0.0 |

## Root causes, ranked

The families cascade: a name that is never declared fences as name-resolution on every later read, so the fence count ranks the symptom. A script is released by the root cause when every family it carries belongs to that cause. These are the epics, and the prize each one holds.

| root cause | scripts released |
| --- | --- |
| inference | 3 |
| call-table | 1 |
| statements | 1 |

## Stdlib modules, ranked

The stdlib module each fence names, and how many scripts it holds back. A module with a high demand count and a low blocked count is already first class; a module with a high demand count and a high blocked count is the next grind.

| module | scripts blocked |
| --- | --- |
| os | 3 |
| time | 3 |
| json | 2 |
| calendar | 1 |
| dataclasses | 1 |
| itertools | 1 |
| platform | 1 |
| pylong | 1 |
| pyperf | 1 |
| random | 1 |
| sqlite3 | 1 |
| subprocess | 1 |
| zipfile | 1 |

## Scripts

| script | compile | fences | top blocking features |
| --- | --- | --- | --- |
| batch_file_rename | fenced | 4 | return-inference (2), param-inference (1), unlowered-call (1) |
| black_schema | fenced | 1 | dynamic-annotation (1) |
| black_width_table | fenced | 1 | module-assign (1) |
| brute_force | fenced | 10 | param-inference (2), stdlib:time (2), unlowered-call (2) |
| cat | fenced | 6 | param-inference (3), return-inference (2), unlowered-call (1) |
| check_file | fenced | 4 | return-inference (2), param-inference (1), unlowered-call (1) |
| checksum_script | fenced | 5 | param-inference (3), return-inference (1), unlowered-call (1) |
| combinerefs | fenced | 4 | param-inference (2), return-inference (1), unlowered-call (1) |
| daily_checks | fenced | 8 | return-inference (5), import-table (1), param-inference (1) |
| dataclass_config | fenced | 7 | construct-other (3), forward-reference (1), parameter-forms (1) |
| day_of_week | fenced | 6 | param-inference (2), call-arity (1), import-table (1) |
| divmod_threshold | fenced | 6 | param-inference (2), import-table (1), return-inference (1) |
| duplicate_file_finder | fenced | 31 | name-resolution (13), local-inference (4), operator (4) |
| file_dispatcher | fenced | 3 | call-arity (2), keyword-args (1) |
| file_ext_changer | fenced | 29 | name-resolution (16), local-inference (6), call-arity (3) |
| file_separator | fenced | 3 | param-inference (1), return-inference (1), unlowered-call (1) |
| folder_size | fenced | 6 | local-inference (3), name-resolution (1), stdlib:os (1) |
| folder_to_zip | fenced | 9 | name-resolution (3), local-inference (2), call-arity (1) |
| itertools_loader | fenced | 4 | annotation-other (1), local-inference (1), name-resolution (1) |
| job_scheduling | fenced | 15 | field-lookup (8), local-inference (3), name-resolution (2) |
| json_to_csv | fenced | 3 | call-arity (1), stdlib:json (1), unlowered-call (1) |
| long_conv_tables | fenced | 4 | param-inference (2), return-inference (1), unlowered-call (1) |
| move_files_over_x_days | fenced | 14 | name-resolution (9), local-inference (4), keyword-args (1) |
| network_troubleshooter | fenced | 15 | name-resolution (8), return-inference (3), unlowered-call (3) |
| organise | fenced | 7 | param-inference (2), unlowered-call (2), call-arity (1) |
| organise_by_extension | fenced | 13 | name-resolution (8), local-inference (4), call-arity (1) |
| ping_servers | fenced | 19 | local-inference (13), name-resolution (6) |
| powerup_checks | fenced | 11 | local-inference (3), return-inference (3), name-resolution (2) |
| puttylogs | fenced | 3 | local-inference (1), name-resolution (1), stdlib:os (1) |
| random_file_move | fenced | 13 | name-resolution (7), local-inference (3), keyword-args (1) |
| repo_info | fenced | 4 | return-inference (2), module-assign (1), name-resolution (1) |
| reveal_md | fenced | 17 | name-resolution (9), local-inference (5), stdlib:json (2) |
| smart_file_organizer | fenced | 15 | name-resolution (12), construct-other (1), keyword-args (1) |
| snake_case_renamer | fenced | 14 | name-resolution (10), local-inference (3), keyword-args (1) |
| sortperf | fenced | 38 | name-resolution (11), unlowered-type-name (10), local-inference (5) |
| ssh_host_script | fenced | 4 | param-inference (2), return-inference (1), unlowered-call (1) |
| time_delta | fenced | 14 | local-inference (6), name-resolution (6), construct-other (2) |
| webserver_log | fenced | 8 | name-resolution (5), param-inference (1), return-inference (1) |
| work_connect | fenced | 8 | stdlib:subprocess (4), local-inference (2), call-arity (1) |
