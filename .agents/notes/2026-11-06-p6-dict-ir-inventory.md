# P6 dict-IR consumer inventory

Measured raw dict-IR reads in the drop-package P6 modules. Rank is the
sum of AST `obj["key"]` and `obj.get("key")` sites for the keys
`kind`, `functions`, `classes`, `body`, `span` (the handoff's count).
`dict_build` is hand-built `{"kind":` records. `typed_use` is the
`emit/_ir` / `_irnode` import-or-call count. Role is produce when the
module lowers or merges a module dict; consume when it only reads one.

Regenerate: `uv run python scripts/dict_ir_consumers.py --markdown FILE`.

| rank | module | reads | kind | functions | classes | body | span | dict_build | typed_use | role |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | `binding.py` | 16 | 14 | 1 | 1 | 0 | 0 | 0 | 1 | consume |
| 2 | `debug.py` | 7 | 1 | 3 | 0 | 0 | 3 | 0 | 0 | consume |
| 3 | `coverage.py` | 6 | 0 | 0 | 0 | 0 | 6 | 0 | 0 | consume |
| 4 | `package.py` | 6 | 0 | 1 | 1 | 0 | 4 | 4 | 0 | produce |
| 5 | `assess.py` | 5 | 0 | 4 | 1 | 0 | 0 | 0 | 0 | consume |
| 6 | `boxed.py` | 5 | 0 | 3 | 2 | 0 | 0 | 1 | 0 | consume |
| 7 | `dwarf.py` | 5 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | consume |
| 8 | `project.py` | 5 | 2 | 3 | 0 | 0 | 0 | 16 | 0 | produce |
| 9 | `bundle.py` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | consume |
| 10 | `cli/_report.py` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | consume |
| 11 | `emitlib.py` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | consume |
| 12 | `llvverify.py` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | consume |
| 13 | `mobile.py` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | consume |
| 14 | `reduce.py` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | consume |
| 15 | `reflection.py` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | consume |

## Conversion order

Follow this ranking. Skip a module whose `reads` is 0. Grouped PRs
from the handoff stay grouped, in the order their members first appear:

1. **P6-f binding, emitlib, bundle, boxed** (21 reads): `binding.py`, `emitlib.py`, `bundle.py`, `boxed.py`
2. **P6-a report surface** (7 reads): `cli/_report.py`, `debug.py`
3. **P6-b coverage.py** (6 reads): `coverage.py`
4. **P6-c package.py** (6 reads): `package.py`
5. **P6-e assess.py and reduce.py** (5 reads): `assess.py`, `reduce.py`
6. **P6-d project.py** (5 reads): `project.py`

## Notes

- `cli/_report.py` is the coverage/imports/reduce CLI; it does not
  walk the module dict. The `--debug` summaries live in `debug.py`.
- `package.py` both produces the merged module and reads `functions` /
  `classes` / `span` while merging; keep the on-disk IR shape unchanged.
- `assess.py` `get("functions")` / `get("classes")` hits are the
  fallback plane map, not the IR function list. They still match the
  named keys, so they count; the conversion should not treat them as
  IR records.
- `boxed.py` likewise reads plane-map `functions`/`classes` and IR
  function/class `name` records. Boxed markers stay.
- `emitlib.py`, `bundle.py`, `reflection.py`, `mobile.py`,
  `llvverify.py` have zero of the named reads on this snapshot.
- `reduce.py` is zero on the five keys; it still reads `module.get("fences")`.
  Convert it with `assess.py` as the grouped PR.
- `dwarf.py` has 5 `kind` reads and is not in a grouped conversion PR;
  convert it after the grouped list.
- Frozen scorer `benchmarks/compat2/coverage_map.py` is not in this set.

