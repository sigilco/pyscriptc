# DR-1: v0.10.0 dict-IR deletion map

Date: 2026-11-06. Epic: #817. Milestone: v0.10.0 (the drop). Docs only; no code, no frozen-file edits. Measured on master `1c0b88be`.

v0.10.0 mechanically deletes the old dict-IR compiler path once the typed-HIR/MIR path is the only one. This note is the checklist that makes that deletion mechanical: every module, test, CI job, script and benchmark that exists only for the dict IR, what replaces it, and which PR or issue removes it. Sibling inventories: `.agents/notes/2026-11-05-dict-ir-test-inventory.md` (T-0 `"kind"` tests), `.agents/notes/2026-11-06-dict-ir-serde-inventory.md` (R-3 serializers), `.agents/notes/2026-11-06-retire-ir-hashes.md` and `.agents/plans/2026-11-05-ir-hashes-retirement.md` (R-1 / #830), `.agents/notes/2026-11-06-emit-raw-reads-inventory.md` (E-4 / #829, PR #990). Open retirement PRs at write time: #973, #980 (both docs-only, unmerged).

Method: `uv run python scripts/compiler_progress.py` (the #951 seam tracker: per-module `lines`, `dict_build`, `dict_read`, `typed_use` over `lower/`, `mir/`, `emit/`), `scripts/dict_ir_consumers.py` (the P6 raw-read ranking), and `rg`/`wc -l` over the tree. Every count below comes from the tree at `1c0b88be`; where a file mixes dict and typed code it is a split candidate, not a deletion.

## 1. Modules that exist only for the dict-IR path

These files have no typed-HIR or MIR reason to exist; each dies whole in the drop wave.

| Module | Lines | What it is | Replaced by | Removed by |
| --- | --- | --- | --- | --- |
| `src/pyscriptc/canonical.py` | 167 | Dict-shape canonicalizer: `canonicalize` strip/sort/path-scrub, `hash_module` compact-JSON SHA-256, `CANONICAL_SCHEMA_VERSION` | `tirserde` typed streaming hasher + `TIR_HASHES.json` (P2 of #830) | #830 D2 (path in #973/#980) |
| `src/pyscriptc/ir.py` | 558 | Dict module schema: `validate_module`, `IR_VERSION`, `SUBSET`, `TYPE_KINDS`/`EXPR_KINDS`, `assert_same_cpython`, `fits_i64` | Typed-HIR module validation; `fits_i64`/`cpython_version` already re-exported through `emit/_ir.py` | #830 D3 |
| `src/pyscriptc/irverify.py` | 427 | Soundness verifier over the dict IR | Typed-node verifier (rewrite or replace; #830 keeps it until `validate_module` leaves the compile path) | #830 D3 |
| `src/pyscriptc/coro.py` | 2222 | Dict IR-to-IR resumable pass (`lower_resumables`, frame classes, `__ps_yield__`/`__ps_await__` markers) | Typed coroutine/generator lowering (R-2; design PRs #974/#978) | R-2 implementation PR, then v0.10.0 drop |
| `src/pyscriptc/lower/_coro_adapter.py` | 44 | `node_to_dict` seam converting lowered records into the dict IR `coro.py` reads | Nothing — it exists only to feed `coro.py` | Same PR as the typed coro port (R-2) |
| `src/pyscriptc/emitter.py` | 76 | `make_module`/`emit_ir_text` dict-module seam for the emitter goldens | Typed or MIR fragment seam (owner decision in the serde note; #829 emit migration) | v0.10.0 drop PR, with the golden format decision |
| `src/pyscriptc/emit/_ir.py` | 930 | Dict IR-reading seam for every emitter (`_kind`, `_expr_kind`, `llvm_type`, `fn_symbol`, `ir_const_value`, ...) | MIR reads in `emit/llvm/*` (#829, one mixin per PR) or typed-HIR reads | #829 completion, confirmed by the v0.10.0 drop |

Notes on near-misses that are **not** deletions:

- `src/pyscriptc/llvm.py` (30) is a re-export shim for `pyscriptc.emit.llvm`; it dies only if the emit package is renamed, not because of the dict drop.
- `src/pyscriptc/mir.py` (746), `mirlower.py` (827), `miremit.py` (732), `mirverify.py` (433) are the MIR lane. They consume the dict module today (`mirlower.lower_module(module: Dict[str, Any])`, `miremit` behind `PYSC_MIR`/`mir_opt_in` in `cli/_main.py`) and become typed-HIR consumers; they are the replacement, not the deleted.
- `src/pyscriptc/_irnode.py` (1486) is the typed HIR itself; it stays.
- `src/pyscriptc/runtime/` C sources are shared by both paths; untouched.

## 2. Split candidates (dict and typed share the file)

These files carry both paths; the drop removes the dict half, not the file.

| Module(s) | Lines | Dict half that dies | Typed half that stays |
| --- | --- | --- | --- |
| `src/pyscriptc/tirserde.py` | 949 | Dict-side serde: `payload_bytes`/`hash_module` over `canonicalize(module)` (lines 194-215), `node_to_dict` (464), `dict_to_type`/`dict_to_span`/`dict_to_node` (625-665), the `canonical` import (line 90) | `payload_bytes_node`/`hash_module_node` once they stop reconstructing the dict (P2 of #830) |
| `src/pyscriptc/lower/` (23 files) | 17,255 | `node_to_dict` reconstruction at every return (17 files import `tirserde` serde; heaviest `_calls.py` 73 matches, `_stmt_b.py` 55, `_core.py` 37, `_call_attr.py` 26) and residual `{"kind":` literals / `["kind"]` reads (`dict_build` 272, `dict_read` 184 per `compiler_progress.py`) | The typed-node builders (`typed_use` 237) and the per-file lowering logic itself |
| `src/pyscriptc/restlower/` (34 files) | 6,184 | Dict-key reads in handlers (`["kind"]`/`.get("kind")` counts: `_minmax` 16, `_iterators` 14, `_threading`/`_collections`/`_builtins` 13 each, ...) and the builder half of `_shared.py` | `_shared.py` accessors (`_kind`, `_node_kind`, ...) and the per-module call tables; pilot landed in #959 (`_shared`, `_time`) |
| `src/pyscriptc/emit/llvm/` (12 files) + `emit/__init__.py` + `emit/_ir.py` | 7,038 (incl. `_ir.py`) | Dict reads routed through `emit/_ir` accessors today | The mixins themselves, once they read MIR (#829 order: `_stmts`, `_exprs`, `_calls`, `_core`, `_funcs`, `_print`, `_abi` last) |
| `src/pyscriptc/package.py` | 1564 | Module-dict merge/reads (`functions`, `classes`, `fences`, `kind`) — 101 raw-key hits | Package discovery/merge logic on the typed module |
| `src/pyscriptc/deps.py` | 1374 | Lowered-dep module dict plumbing | Dep discovery/lane logic |
| `src/pyscriptc/{coverage,assess,reduce,binding,boxed,dwarf,reflection,mobile,llvverify,emitlib,bundle,project}.py` | 4,857 total | Raw dict-IR reads (the P6 set in `scripts/dict_ir_consumers.py`; e.g. `binding` 59, `dwarf` 49, `boxed` 33, `coverage` 29, `reduce` 29 hits) | Each tool's actual function; several already moved to `emit/_ir` accessors (PRs #983/#984, #990 inventory) |
| `src/pyscriptc/cli/_main.py` + `cli/_report.py` | 2234 + 190 | `.ir.json` write/`--emit ir`/`_handle_run_report` `json.loads`, `validate_module`+`verify_module` stages | Everything else in the CLI |
| `src/pyscriptc/debug.py` | 325 | `--debug-dir ir.json` pretty dump of the module dict | `summary.json`, timings, other debug stages |
| `src/pyscriptc/{sigrec,sigregistry,stdmore,builtins,stubs,imports,stdlib_queue}.py` | 4,727 total | Dict type vocabulary (`type_to_dict`, `{"kind": ...}` type records in signatures — `test_stubs_registry.py` pins this) | The signature tables themselves once they carry `T` |

## 3. Tests and fixtures deleted rather than migrated

Cross-references the 2026-11-05 inventory. T-1 has mostly landed: of the 22 class (a) files only 5 still contain `"kind"` (`test_ffi_lower`, `test_t2_static_folds`, `test_spike`, `test_island_scope_handles`, `test_e1_negstep` — leftover lines, not whole files). The rows below are the delete-at-drop set.

| Test / fixture | Lines / count | Why it dies | Removed by |
| --- | --- | --- | --- |
| `tests/test_canonical.py` | 283 | Pins dict-shape canonical rules on purpose (class b) | #830 D2 |
| `tests/test_irverify.py` | 640 | Hand-built dict modules per verifier failure class | #830 D3 (rewrite against typed nodes or delete) |
| `tests/test_irnode_serde.py` | 274 | `node_to_dict` golden equality — the dict serializer contract | v0.10.0 drop PR (with the `tirserde` dict half) |
| `tests/test_tirserde_island_call.py` | 36 | Round-trips dict leftovers `dict_to_node` keeps as dicts | Same as above |
| `tests/test_irnode_import.py` | 150 | `hash_module_node` vs `hash_module` byte-identity across the dict boundary | #830 P2/D2 wave |
| `tests/test_irscore.py` | 254 | Drives `irscore`/`IR_HASHES.json` | #830 D4 |
| `tests/test_emitter_seam.py` | 224 | `make_module`/`emit_ir_text` take dict fragments; corpus rebuild compares dicts | v0.10.0 drop PR, with `emitter.py` and the golden format |
| `tests/test_emitter_package.py` | 607 | Pins `emit/_ir` accessors against `{"kind": ...}` dict fragments (the emit-package split assertions survive) | Split: dict-fragment tests die with `emit/_ir.py` (#829 done); package-shape tests stay |
| `tests/test_emitter_goldens.py` + `tests/fixtures/backend/llvm/*.ir.json` | 134 + 36 fixtures (+ `manifest.json`, 465 lines) | Committed dict-IR modules paired with LLVM goldens | v0.10.0 drop PR; format is an owner decision (typed pretty-print vs generated-from-builders vs MIR text — serde note section "What a typed on-disk format would require") |
| `tests/test_stubs_registry.py` | 221 | Lowering signatures store dict types | Survives until signatures carry `T`; then rewrites, not deletes |
| `tests/test_ffi_manifest.py` | 1050 | `type_ir()` returns dict types as its FFI contract | Same — survives until `type_ir` returns `T` |
| `tests/test_debug.py` | 236 | Asserts `--debug-dir/ir.json` byte-equals `<out>.ir.json` | Survives if the dump format is kept; rewritten with the dump decision |
| `tests/test_p6_ir_seam.py`, `tests/test_ir_validate_seam.py`, `tests/test_dict_ir_consumers.py`, `tests/test_restlower_typed_nodes.py` | — | Seam/inventory guards for the dict->typed migration itself | Each dies or inverts when its seam completes (P6 done -> `test_dict_ir_consumers` has nothing to rank) |
| Pretty-`.ir.json` consumers (~20 files: `test_band`, `test_capability`, `test_cli_dx`, `test_coverage`, `test_deps_*`, `test_fallback_definitions`, `test_first_run`, `test_lwave`, `test_package`, `test_program`, `test_r26_optional`, `test_r29_collections`, `test_reach_pruning`, `test_reexports`, `test_spike`, ...) | — | `json.loads` the dump for `fences`/module shape | Migrate with the `--emit ir` format decision; most survive as behavior tests |

Incidental `"kind"` tests (`test_checker_adapters`, `test_pyrefly_tsp_facts`, `test_imports`, `test_case_reducer`, `tests/compat2/*`) are not compiler IR and are untouched.

## 4. CI jobs, scripts and benchmarks that exist only for the dict path

| Surface | What it does | Removed by |
| --- | --- | --- |
| `.github/workflows/ir-hashes.yml` | PR compare of `irscore` vs `IR_HASHES.json` + generate/`cmp` dispatch (not a required check) | #830 D1 (switch to tirscore first per P1, then remove) |
| `benchmarks/compat2/irscore.py` | Dict-IR probe lowering + `canonical.hash_module` driver, 214 lines | #830 D4 (delete with the JSON file) |
| `benchmarks/compat2/probes/IR_HASHES.json` | 13,692-probe dict-shape hash baseline (frozen) | #830 D4 — delete, never hand-edit or regenerate |
| `benchmarks/compat2/fixpoint.py` | Self-vs-self per-module hashes via `canonical.hash_module`, 145 lines | Survives; P3 of #830 switches it to `tirserde` |
| `benchmarks/compat2/fndigest.py` | Loads `.ir.json` dumps / `lower_file` dicts, hashes per function/class, 102 lines | #830 D2 — switch to the TIR hasher or delete |
| `benchmarks/compat2/tirscore.py` | Streaming TIR gate vs `TIR_HASHES.json`, 245 lines | Survives; becomes the only canonicity gate |
| `mise` task `ir:hashes` | Wraps the irscore compare | #830 D1 (alias to `tir:hashes` or delete) |
| `scripts/gen_emitter_goldens.py` | Builds dict modules, writes the 36 committed fixtures, 1311 lines | v0.10.0 drop PR — regenerate typed or delete with the fixture format |
| `scripts/dict_ir_consumers.py` | P6 raw dict-read ranking, 220 lines | Dies when the P6 read count reaches zero (or is repointed at typed seams) |
| `scripts/compiler_progress.py` | Seam tracker (`dict_build`/`dict_read`/`typed_use` per module), 336 lines | Survives — its dict counters are the drop's progress meter (#951 prints the trend) |
| `scripts/mir_coverage.py`, `scripts/mir_perf_ratio.py` | MIR leaf coverage/perf over the visible corpus | Survive — already MIR-side |
| `tests/census_pins.json`, `scripts/repin_census.py`, `tests/test_census_self.py`, `benchmarks/compat2/census*.py` | Self-host fence census | Survive — fence counts, not dict shape; repin when modules are deleted (gates require `test_census_self` green) |
| `.agents/docs/spec/ir-canonicity.md`, `docs/reference/stability.md`, `docs/reference/cli.md`, `docs/guide/debugging.md`, `.agents/docs/debug.md` | Canonical-IR spec + `--emit ir`/`ir.json` user docs | #830 D2 marks spec sections historical; user docs update with the dump-format decision |

## 5. Order of deletion

What must land **before** the v0.10.0 drop PR can be mechanical:

1. **Typed HIR is the only lowering output.** `lower_source`/`lower_file` stop returning `dict[str, Any]`; every `lower/` and `restlower/` return path emits `_irnode` records with no `node_to_dict`. The `compiler_progress.py` `dict_build`/`dict_read` counters reach zero under `lower/`.
2. **`coro.py` is typed (R-2).** `lower/_coro_adapter.py` deleted in the same PR. Gate: `test_coro_*` green without the adapter.
3. **Emit reads MIR or typed HIR (#829 complete).** Every `emit/llvm` mixin migrated; `emit/_ir.py` has no remaining caller; `emitter.py`/`test_emitter_seam.py` re-pointed or deleted with the golden format decision.
4. **Consumers on typed accessors (P6 done).** `package`, `deps`, `coverage`, `assess`, `reduce`, `binding`, `boxed`, `dwarf`, `reflection`, `mobile`, `llvverify`, `emitlib`, `bundle`, `project`, `debug`, `cli/_report` read the typed module; `scripts/dict_ir_consumers.py` reports zero.
5. **Hash gate retired (#830 signed).** P0-P3 landed: TIR PR compare exists, `hash_module_node` streams typed nodes, `fixpoint` on `tirserde`, class (b) hash tests classified. D1-D2 land: `ir-hashes.yml` off irscore, `canonical.py` deleted, `tirserde` no longer imports `canonical`.
6. **Dump format decided (owner).** Whether `--emit ir`/`--debug-dir ir.json` stays public JSON produced from typed nodes, becomes a typed dump under a new flag, or is removed; emitter goldens regenerated or deleted. User docs updated in the same PR.

Then the drop PR is a mechanical deletion, in this order (each step its own commit or PR if it does not stay green atomically):

- D-a. `canonical.py` + `tests/test_canonical.py` (already done at #830 D2; listed for completeness).
- D-b. `ir.py` + `irverify.py` + `tests/test_irverify.py` (#830 D3; `validate_module`/`verify_module` off the CLI path first).
- D-c. `coro.py` + `lower/_coro_adapter.py` + `tests/test_irnode_serde.py` + `test_tirserde_island_call.py` (with R-2 and the `tirserde` dict half).
- D-d. `emit/_ir.py` + `emitter.py` + `emit/llvm` dict remnants + `tests/test_emitter_{seam,package,goldens}.py` + the 36 fixtures + `scripts/gen_emitter_goldens.py` (with #829 completion and the golden format).
- D-e. `benchmarks/compat2/irscore.py` + `probes/IR_HASHES.json` + `.github/workflows/ir-hashes.yml` + `mise ir:hashes` + `tests/test_irscore.py` + `fndigest.py` (#830 D4).
- D-f. The `tirserde` dict half (`node_to_dict`/`dict_to_node`/`dict_to_type`/`dict_to_span`, dict `payload_bytes`/`hash_module`) once no caller remains.
- D-g. Re-run `scripts/compiler_progress.py` and `scripts/dict_ir_consumers.py`; both should report zero dict sites under `src/pyscriptc`. Census repin for deleted modules.

## Gaps

- Line counts are `wc -l` at `1c0b88be`; the tree moves daily — re-run the commands before the drop PR.
- `irverify`/`coro`/`canonical` substring greps also match `mirverify`/coroutine text; the module table above was checked by import, not by raw count alone.
- The class (a) leftover `"kind"` lines in 5 test files were counted, not classified line-by-line; they may be accessor tests that survive.
- Whether `--emit ir` stays public JSON is an open owner decision (serde note); D-d's fixture deletion waits on it.
