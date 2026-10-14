# Iteration loop

The goal is still open. A run counts only when one of the five KPI numerators below increases. Partial does not count. A pytest increase, a packaging fence-count change, a line in a progress log, a first-fence string change, and a flat percent are not KPI movement. Do not shrink a denominator. Do not edit past snapshots to move a number. Do not edit `benchmarks/compat/score.py`, `benchmarks/compat/denominators.json`, `benchmarks/compat/delta.py`, `tests/test_compat.py`, the top-100 pins, `gates-v1`, or `IR_VERSION`.

## KPIs

Copy these integers from the scorer snapshot (`scores` in `benchmarks/compat/iterations/*.json`). Use `lowered` for the first four and `subset` for top 100 (`lowered_or_subset` on top 100 is that subset count).

| KPI | Floor |
| --- | --- |
| language lowered | 45/56 |
| builtins lowered | 120/150 |
| stdlib modules lowered | 156/194 |
| stdlib surface lowered | 1983/2478 |
| top 100 subset | 10/100 |

## Fail-fast

1. Before editing the compiler, name the scored id or package this iteration will move, and its current status (`fenced` or `partial`). Read the status from the latest `benchmarks/compat/iterations/*.json` (ignore `*runtime.json`) and read the probe from the harness. Do not edit the harness.
2. The named id has to be one the scorer can change from `fenced` or `partial` to `lowered`, or the named package has to be one it can change to `subset`. If you cannot name one, stop before editing the compiler. Append a failed-run record and do not commit compiler changes.
3. After `python -m benchmarks.compat.score`, diff the five numerators against the previous snapshot. If none increased, revert the compiler edit, delete the new snapshot so the previous snapshot stays the latest, append a failed-run record, and do not keep the iteration commit.
4. Do not repeat a behavior listed in `benchmarks/compat/failed.jsonl`. Match on the `behavior` field.
5. Iteration 021 (`expr-compare-chain`) is already running and is allowed to finish. It names a language row the scorer counts (`expr-compare-chain`, status `fenced` in `020-nan-in.json`). Do not record it as failed in advance. If it finishes with language still 34, the worker who finishes it appends the failed-run record then.

## Failed-run record

Append one JSON object as a single line to `benchmarks/compat/failed.jsonl`. Fields: `iteration` (snapshot file name, or `""` when you stopped before a snapshot), `behavior` (short name of the attempt), `why` (why it cannot move a KPI), `numerators` (`language`, `builtins`, `stdlib_modules`, `stdlib_surface`, `top100`). Copy the numerators from the snapshot you are comparing against. Do not rewrite earlier lines.

## Known dead ends

These are already in `failed.jsonl`. Do not start them again.

- Another packaging site cannot move a KPI. The first fence has been `PS1001: union types are not in the IR` on `ELFFile.interpreter` since `010-tuple-ellipsis.json`. Do not start another packaging fence.
- `model-nan-in` via `==` on NaN bits does not move the row. The probe is `math.nan in [math.nan]`. CPython's result is true by identity. The row is still `partial` in `020-nan-in.json`, and the five numerators stayed at language 34, builtins 28, modules 30, surface 158, top 100 1. Treating equal NaN bits as one object would make `float("nan") in [float("nan")]` true. Do not repeat that attempt.
- A builtin that is not one of the value-checked specials in the harness becomes `partial` when the name compiles, so it cannot raise the builtin numerator. All 28 specials are already lowered as of `020-nan-in.json`. Do not spend an iteration on an unscored builtin name.
- A stdlib module with zero surface names becomes `partial` on a clean import. `os.path` and `zipfile` cannot raise the module or surface numerator that way. `os.path` is a mention-shaped name on `os`, which is already lowered. `zipfile` is in the 194 modules and has no surface names.
- `abc` / `Generic`, unions, `Optional`, user decorators, keyword-only parameters, and unannotated parameters stay outside this loop while a language checklist row that the scorer counts is still fenced. Do not start `http.client` or upstream `requests`.

## Ranked language rows

The rows that can still move language, in order:

1. `expr-compare-chain` (in flight as iteration 021).
2. `expr-list-comp-if`
3. `stmt-break`
4. `stmt-continue`
5. `stmt-assert`
6. `expr-pow` (the probe is `2 ** 3`, lowered as a loop of `*`; do not add an IR kind)
7. `expr-slice-step`
8. `stmt-match`, `stmt-del`, and module-level `stmt-decorator` only if language is still under 45/56.

After 021, start `expr-list-comp-if` if language moved. If 021 left language at 34, record that run in `failed.jsonl` and start the next ranked row that is still fenced, which is `expr-list-comp-if`.

## After the constant batches

Iteration 046 (`046-bool-const-surface.json`, commit `a0186e7e6d62992b8f2cb7b5b68a8e7a16e5299d`) is the current score. Language is 45/56, builtins 28/150, modules 37/194, surface 1206/2478, top 100 1/100.

A batch that stores every remaining int, str, or bool constant on an already-lowered module is one mechanism and may land together. One name of that shape is not an iteration. The int batch (974), the str batch (41), and the bool batch (21) are done. Do not start another constant iteration.

Do not add `urllib.parse` as a module row to reach `urllib.parse.scheme_chars`. That name is the only remaining value-checked fenced surface probe, and `urllib.parse` is not a lowered module row. One string is not worth a new module.

Do not spend an iteration on a language row, including `expr-star`, while language is at or above 45/56 and that row cannot move builtins, modules, surface, or top 100. The builtin numerator stays capped at 28: only the value-checked specials count, and `score.py` stays frozen. Mention-only modules (`contextlib`, `dataclasses`, `enum`, `itertools`, `typing`) cannot raise the module numerator. The harness caps modules at 37.

The outside list stays outside: `abc` / `Generic` (`iniconfig`, `platformdirs`, `pathspec`, `filelock`), unions (`packaging`), `Optional` (`python-dotenv`), keyword-only parameters (`mdurl`), unannotated parameters (`jmespath`), and user decorators (`h11`). Top 100 is still 1 (`certifi`). The next top-100 movement requires one of those features. This file does not authorize starting one. Do not start `http.client` or upstream `requests`.
