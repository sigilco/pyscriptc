# Raw IR dict reads left under `emit/` (#829, batch 7)

Date: 2026-11-06. Epic: #817. Milestone: v0.9.0. Docs only.

What still reads IR records by raw key under `src/pyscriptc/emit/` after the batch 7 mixin PRs, measured on `master` at `25d9cb94` plus the `_exprs` PR (#989).

## How it was counted

- Plan regex (the one every batch 7 task sizes against): `rg -c '\["(kind|type|id|name)"\]|\.get\("' FILE`.
- Broad count (any string-key subscript or `.get(`): `rg -c -e '\["[a-zA-Z_]+"\]' -e "\['[a-zA-Z_]+'\]" -e '\.get\(' FILE`.

The broad count also hits reads of non-IR tables (exception codes, math specs, timing dicts, LLVM decl lists). The "IR reads" column below only counts the reads that open an IR record.

## Counts by file

| File | Plan regex | Broad | IR reads | What remains |
| --- | --- | --- | --- | --- |
| `emit/_ir.py` | 31 | 87 | n/a | The accessors themselves; this is the seam, expected to stay. |
| `emit/llvm/_stmts.py` | 24 | 70 | ~70 | Statement children (`value`, `target`, `cond`, `then`, `else`, `body`, `finalbody`, ...). Covered by open #947. |
| `emit/llvm/_core.py` | 9 | 20 | 20 | Module, class, method and closure-wrapper records (`functions`, `classes`, `fields`, `methods`, `params`, `returnType`, `index`). Covered by open #950. |
| `emit/llvm/_driver.py` | 1 | 9 | 5 | Module record (`fences`, `fallback.moduleTopLevel`, `sourceFile`, `functions`). The other 4 are `os.environ` and `timings`. Covered by open #950. |
| `emit/llvm/_print.py` | 0 | 5 | 5 | `expr["sep"]`, `expr["end"]` (guarded by `_has`), and `class_index[...]["fields"]` twice plus one `class_index.get`. |
| `emit/llvm/_funcs.py` | 0 | 3 | 2 | `fn["returnType"]`, `stmt["value"]`. `functions.get(name)` is an emitter table lookup. Covered by open #950. |
| `emit/llvm/_calls.py` | 0 | 3 | 0 | `self.functions.get(...)` lookup and `["ptr"]` list literals. |
| `emit/llvm/_abi.py` | 0 | 9 | 0 | Kind-code maps, `self.functions` / `partial_keys` / `thread_keys` lookups, `EXCEPTIONS[...]`, `["ptr"]` list literals. |
| `emit/llvm/_runtime.py` | 0 | 13 | 0 | `MATH_FUNCTIONS` sig specs, `EXCEPTIONS[...]`, the zero-division message map. No IR records; E-3 skipped. |
| `emit/llvm/_stdlib.py` | 0 | 1 | 0 | `spec["llvm"]`, `spec["symbol"]` on a `MATH_FUNCTIONS` sig. No IR records; E-2 skipped (done by #900 and #948). |
| `emit/llvm/_base.py` | 0 | 0 | 0 | Nothing left; E-3 skipped. |
| `emit/llvm/_exprs.py` | 0 | 0 | 0 | Moved in #989 (24 plan-regex, 36 broad before). |
| `emit/__init__.py`, `emit/llvm/__init__.py` | 0 | 0 | 0 | Nothing left. |

## Gaps after batch 7

- Once #947 and #950 merge, the only IR reads outside `_ir.py` are 5 in `_print.py` (class `fields` via `class_index`, and the `sep`/`end` children). No open PR covers them. They need two accessors (`_print_sep`/`_print_end`, or reuse `_opt_record`) and reuse of the existing `_class_fields`.
- `self.functions.get(...)` and `self.class_index.get(...)` look up emitter-built tables keyed by name, not IR records. The plan regex does not count them; moving them through the seam would mean `_ir.py` owning those tables.
- The `.get("` part of the plan regex misses `.get(` with a non-literal key and single-quoted subscripts (`p['type']` in `_core.py` lines 395 and 396). Use the broad count to call the migration complete.
