# T-0: tests that mention `"kind"` (handoff 5)

Date: 2026-11-05. Epic: #817. Measured on master `ff851716` with the command the plan names: `rg -l '"kind"' tests/ | sort`. 76 files, of which 40 are Python tests and 36 are emitter golden IR fixtures.

The `"kind"` string is not always the dict IR. Probe metadata, TSP/pyrefly type-server JSON, import-table lanes, and reducer step records all use the same key. Those rows are listed so later tasks do not migrate them by accident.

There is no typed module envelope yet (`lower_source` still returns `dict[str, Any]`; functions, classes, fences, and `entry` stay dict records). Class (a) migrations convert statement, expression, callee, and type payloads through `pyscriptc.tirserde.dict_to_node` / `dict_to_type` and assert on `.kind` / `.id` / `.op` / `.svalue` / `T` fields. They must not call `node_to_dict` in the assertion (that would re-pin the dict shape). The module envelope (`mod["functions"]`, `mod["fences"]`, `mod["entry"]`) stays keyed until a typed Module lands; that is not a `"kind"` pin.

Class (b) stays until the R-1 retirement of `IR_HASHES.json` and the dict-shape canonical rules, or until the API under test stops returning dict types.

## Counts

- 36 class (c) fixtures: `tests/fixtures/backend/llvm/*.ir.json` (every file that command listed under that directory).
- 22 class (a) Python tests: migrate in T-1, at most 6 files per PR.
- 9 class (b) Python tests: keep pinning the dict shape on purpose (including `test_stubs_registry.py`).
- 9 incidental Python tests: `"kind"` is not the compiler dict IR.

## Class (a): migrate to typed accessors or behavior

Same assertion strength. Test names do not change. Hash pins inside these files (`hash_module(...)` in `test_e1_append.py` and `test_e1_entry.py`) stay; they are class (b) lines inside an otherwise migratable file.

| File | Hits | What `"kind"` is doing | Migration |
| --- | --- | --- | --- |
| `tests/test_e1_append.py` | 7 | callee/type/stmt shape of `list.append` | `dict_to_node` on the push call and the expr stmt; `dict_to_type` on list/int/none types. Keep the `hash_module` pin. |
| `tests/test_e1_assert.py` | 7 | assert desugars to `if`/`unary not`/`raise`/`const` | typed walk of probe and entry bodies. |
| `tests/test_e1_entry.py` | 5 | entry body kinds, assign walk, inlined const | typed walk; keep `hash_module`; `returnType` via `dict_to_type`. |
| `tests/test_e1_fortuple.py` | 3 | while/assign/list filters on the entry | typed filters on body nodes. |
| `tests/test_e1_intbytes.py` | 4 | return/call/callee/type of `int(bytes)` | typed call nodes. |
| `tests/test_e1_print.py` | 4 | body kinds and builtin print callee | typed stmt/callee/type. |
| `tests/test_e1_negstep.py` | 11 | slice call args, unary prelude, temps | typed call and prelude nodes. |
| `tests/test_e1_sortedkey.py` | 1 | optional return elem type | `dict_to_type` on `returnType`. |
| `tests/test_e1_sysargv.py` | 2 | sys.argv callee and `list[str]` type | typed call nodes. |
| `tests/test_spike.py` | 4 | entry body kinds, while presence, bad `f64` type for `ir.py` | typed body walk; the `returnType = {"kind": "f64"}` mutation is a validator input, keep as a dict (that is `ir.py`'s current contract, class (b) line). |
| `tests/test_optional_widening.py` | 3 | param type dicts for `int \| None` and `list[int \| None]` | `dict_to_type` and `T` fields (`optional` is `flags & F_TOPT`). |
| `tests/test_factsbridge.py` | 3 | local and return type dicts after a bridge | `dict_to_type` on those records. |
| `tests/test_ffi_agg.py` | 6 | call/subscript kinds and container types | typed body nodes. |
| `tests/test_ffi_handles.py` | 3 | handle class type and `try` stmt | `dict_to_type` plus typed body. |
| `tests/test_ffi_lower.py` | 5 | call type, const return, walk by kind | typed walk; const value via `.svalue` / `_const_value`. |
| `tests/test_island_scope_handles.py` | 1 | find `try` while walking a body | typed walk. |
| `tests/test_t2_kwargs.py` | 11 | packed kwargs dict/list/const args | typed call args (`kind`, `pairs`). |
| `tests/test_t2_static_folds.py` | 1 | walk looking for `kind == call` | typed walk. |
| `tests/test_coro_async.py` | 2 | resume locals are `coro.value` / `int` | `dict_to_type` on those local types. |
| `tests/test_coro_generators.py` | 1 | user locals include `gen.count` | `dict_to_type` on local types. |
| `tests/test_coro_tasks.py` | 1 | task type `elem` is int | `dict_to_type` on the task types. |
| `tests/test_stdlib_argparse.py` | 7 | Namespace field type kinds | `dict_to_type` per field. |

Suggested T-1 grouping (independent PRs, each at most 6 files):

1. E1 statements A: `test_e1_append.py`, `test_e1_assert.py`, `test_e1_entry.py`, `test_e1_fortuple.py`, `test_e1_intbytes.py`, `test_e1_print.py`.
2. E1 remainder plus spike/types: `test_e1_negstep.py`, `test_e1_sortedkey.py`, `test_e1_sysargv.py`, `test_spike.py`, `test_optional_widening.py`, `test_factsbridge.py`.
3. FFI, island, kwargs: `test_ffi_agg.py`, `test_ffi_handles.py`, `test_ffi_lower.py`, `test_island_scope_handles.py`, `test_t2_kwargs.py`, `test_t2_static_folds.py`.
4. Coro plus argparse: `test_coro_async.py`, `test_coro_generators.py`, `test_coro_tasks.py`, `test_stdlib_argparse.py`.

## Class (b): pin the dict shape on purpose

| File | Why it stays |
| --- | --- |
| `tests/test_canonical.py` | Builds a hand-made module dict and asserts canonicalize/hash. This is the dict-shape canonical oracle. Retires with R-1. |
| `tests/test_irverify.py` | Hand-built dict modules for each verifier failure class. `irverify.py` still reads the dict schema. |
| `tests/test_irnode_serde.py` | `node_to_dict` golden equality. This is the serializer contract that keeps TIR bytes identical to the dict payload. |
| `tests/test_tirserde_island_call.py` | Round-trip of island/print extra keys that `dict_to_node` deliberately leaves as dicts. |
| `tests/test_irnode_import.py` | One test loads a JSON module dict and checks canonical bytes against typed body nodes. The envelope is the dict module the serializer still hashes. |
| `tests/test_emitter_seam.py` | `make_module` / `emit_ir_text` take dict fragments. The seam is still dict-shaped. |
| `tests/test_emitter_package.py` | Pins `pyscriptc.emit._ir` accessors against dict fragments (`{"kind": ...}`). Those accessors still index the dict. |
| `tests/test_ffi_manifest.py` | `type_ir()` returns dict types as its public FFI contract. Stay until `type_ir` returns `T`. |
| `tests/test_stubs_registry.py` | Lowering signatures store type dicts (`returns == {"kind": "float"}`). The stub table is the dict type vocabulary. Stay until signatures carry `T`. Migrating the tests without changing the API would only wrap and unwrap the same dict. |

`tests/test_emitter_goldens.py` does not itself contain `"kind"` (so the plan command does not list it). It loads the class (c) fixtures. It stays a dict-IR consumer until the goldens grow a typed on-disk format (R-3).

## Class (c): fixtures

36 files under `tests/fixtures/backend/llvm/`, each a committed dict-IR module (`*.ir.json`) paired with LLVM goldens. They are the emitter golden corpus, not tests to rewrite in T-1.

## Incidental: `"kind"` is not the compiler dict IR

| File | What `"kind"` is |
| --- | --- |
| `tests/compat2/test_bundle.py` | Probe metadata field (`kind: "value"`). |
| `tests/test_compat2_calibration.py` | Same probe metadata. |
| `tests/test_compat2_probes.py` | Probe `kind` (`value` / `mention`). |
| `tests/test_irscore.py` | Probe `kind` in synthetic probe JSON. |
| `tests/test_checker_adapters.py` | Pyrefly TSP integer `kind` tags (0, 2, 3, ...). |
| `tests/test_pyrefly_tsp_facts.py` | Pyrefly type-server JSON (`"kind": "instance"`). |
| `tests/test_imports.py` | Import-table `KIND_CURATED`, not an IR node. |
| `tests/test_case_reducer.py` | Reducer step JSON keys `{step, kind, detail, ...}`. |
| `tests/test_lower_split_report.py` | Toy lowerer mock returns `{"kind": "const"}` as a fake type; it is not compiler IR. |

## What T-1 must not do

- Do not weaken an assertion (for example drop a callee check and keep only "it compiled").
- Do not rename tests.
- Do not edit `IR_HASHES.json`, `TIR_HASHES.json`, or other frozen gate files.
- Do not re-bless emitter goldens.
- Do not migrate class (b) files in the same PR as class (a).
