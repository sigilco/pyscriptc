# R-3: inventory of dict-IR serialize and deserialize sites

Date: 2026-11-06. Epic: #817. Milestone: v0.9.0. Handoff 5 group R, task R-3. Docs only; no serializer rewrite, no frozen hash files, no test migration.

This note lists every remaining site that still turns the dict IR into bytes (or back), or that round-trips typed HIR through that dict. Measured on master `58d2f2df` (still an ancestor of current master; #942 after that SHA is emit accessors, not a new dump). Counts come from `rg` over the tree with `benchmarks/compat2/corpus/` and `benchmarks/compat/top100/` excluded. Token matches include definitions, comments, and recursive helpers; unique files are the unit that matters for retirement. The scorer holdout lives under `benchmarks/compat2/corpus/` (see that directory's README); this note does not open holdout contents.

Sibling docs PRs (handoff 5) own the other notes. Cross-link T-0 at `.agents/notes/2026-11-05-dict-ir-test-inventory.md`, R-1 (#830 retirement of `IR_HASHES.json` and `canonical.py` rules) at `.agents/plans/2026-11-05-ir-hashes-retirement.md` (still on open PRs #973 and #980, not yet on master; the decision itself is issue #830), and R-2 (typed coroutine lowering / `coro.py`) at `.agents/plans/2026-11-05-typed-coro-generators.md`. A shorter R-3 sketch at `.agents/plans/2026-11-05-dict-ir-serde-inventory.md` covers the same task without the file-and-match counts; keep this table if both would merge.

Read first: `.agents/plans/2026-11-05-cloud-agent-handoff-5.md`, `.agents/plans/decisions/2026-11-02-tir-gate-streaming-serializer.md`, `.agents/docs/spec/ir-canonicity.md`, `.agents/notes/2026-11-03-pipeline-seams.md`, `.agents/notes/2026-11-04-nightly-gates-clock.md`.

## Headline counts

| Surface | Unique files | Token matches |
| --- | --- | --- |
| `node_to_dict` | 20 | 239 |
| `dict_to_node` | 23 | 292 |
| `dict_to_type` | 13 | 67 |
| `dict_to_span` | 7 | 18 |
| `hash_module` (canonical or streaming) | 10 | 39 |
| `canonicalize(` | 3 | 20 |
| `json.dumps(module` (pretty or in-test) | 9 | 23 |
| `ir.json` / `.ir.json` string in Python | 29 / 27 | 72 / 66 |
| Emitter golden fixtures `*.ir.json` | 36 | (on-disk dict IR) |

Union of those Python files: 69. Add `tests/test_emitter_package.py` and `tests/test_emitter_seam.py`, which deserialize golden IR through `entry["ir"]` without spelling `ir.json`. Total unique Python files: 71. Plus 36 fixture files: 107 paths that still carry or convert the dict IR.

Production `src/pyscriptc` files: 21 (17 `lower/` adapters, `tirserde.py`, `canonical.py`, `cli/_main.py`, `debug.py`). Scripts: 1. Benchmarks: 8. Tests: 41. Fixtures: 36.

On-disk writers of a live module dict as pretty JSON: 3 call sites (`cli/_main.py` beside the artifact, `debug.py` as `--debug-dir/ir.json`, `scripts/gen_emitter_goldens.py` as committed goldens). Canonical compact JSON writers: 2 functions (`canonical.hash_module`, `tirserde.payload_bytes` / `tirserde.hash_module`). Typed-to-dict reconstruction is the large remainder.

## Four formats still in play

Pretty JSON, indent 2, trailing newline. This is the human dump of the live module dict: spans, `sourceFile`, `cpython`, and fences stay. Writers: `src/pyscriptc/cli/_main.py` (`ir_path.write_text(json.dumps(module, indent=2) + "\n")` after validate; every compile writes `<out>.ir.json`, not only `--emit ir`), `src/pyscriptc/debug.py` (`debug_dir / "ir.json"`, same payload as the sibling artifact), `scripts/gen_emitter_goldens.py` (`_dump` = `json.dumps(value, indent=2) + "\n"` into `tests/fixtures/backend/llvm/<name>.ir.json`). Readers: `cli/_main.py` `_handle_run_report` (`json.loads` of `<binary>.ir.json` when `.report.json` is missing, boxed path), `benchmarks/compat2/fndigest.py` `load_module` (`.json` suffix), `benchmarks/library/run.py` fence census, `benchmarks/compat2/bundle.py` fence census, `benchmarks/compat/score.py` (v1, frozen history) `_compile_path`, tests listed below.

Canonical compact JSON. Wrapper `{"schema": CANONICAL_SCHEMA_VERSION, "ir": canonicalize(module)}` then `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`. Spans, `sourceFile`, `cpython`, `_notes` stripped; `classes` and fence records sorted; environment paths scrubbed to `<PATH>`. Writer: `src/pyscriptc/canonical.py` `hash_module` (materializes the payload, SHA-256 hex). Twin: `src/pyscriptc/tirserde.py` `payload_bytes` / `hash_module` stream the same bytes without keeping the string. `tirscore --verify-bytes` is the byte-identity check. Consumers of the digest (not the JSON): `benchmarks/compat2/irscore.py` -> `probes/IR_HASHES.json` (frozen; do not edit), `benchmarks/compat2/tirscore.py` -> `probes/TIR_HASHES.json` (frozen), `benchmarks/compat2/fixpoint.py` per-module hashes, `benchmarks/compat2/fndigest.py` per-function/class units, tests `test_canonical.py`, `test_e1_entry.py`, `test_e1_append.py`, `test_irnode_import.py`.

In-memory typed node to dict and back. `tirserde.node_to_dict` / `dict_to_node` / `dict_to_type` / `dict_to_span`. This is the strangler seam: lowering builds `_irnode` dataclasses and immediately reconstructs the dict the rest of the compiler still reads. `node_to_dict` of a typed module then `payload_bytes` is how `payload_bytes_node` / `hash_module_node` stay byte-identical to the dict path. `lower/_coro_adapter.py` converts functions and classes with `node_to_dict` so `coro.py` can keep walking dicts (R-2 owns that retirement).

Hash-only oracles. `IR_HASHES.json` and `TIR_HASHES.json` store probe id to 64-hex maps plus a header (`canonical_schema`, oracle, algo). They are not a dump of the IR. Regeneration is the run-twice-`cmp` ritual. R-1 owns when the dict-shape file goes away. This inventory must not touch either file.

## Production call sites

`src/pyscriptc/canonical.py`. Only dict-shape canonicalizer. `canonicalize` deep-copies and strips; `hash_module` dumps compact JSON and hashes it. Frozen rules; any change rekeys every `IR_HASHES.json` digest. Typed on-disk format must either keep these strip/sort/scrub rules in the streaming walker or accept a schema bump.

`src/pyscriptc/tirserde.py`. Streaming serializer over `canonicalize(module)` (still a dict walk). Plus the typed reconstruction (`node_to_dict` 21 matches in this file, `dict_to_node` 40, `dict_to_type` 14, `dict_to_span` 2). `payload_bytes_node` / `hash_module_node` call `node_to_dict` then the dict hasher, so a typed dump today still materializes the dict. A typed on-disk format is this file walking `_irnode` field order instead of `canonicalize` output, which is the serializer decision's "later, schema field order" step. It is not done.

`src/pyscriptc/cli/_main.py`. Always writes pretty `<out>.ir.json` after validate. `--emit ir` prints that path and returns. `_handle_run_report` may `json.loads` it. `--output` with `--emit ir` may be a `.ir.json` path (`_output_path`). This is the user-visible dump (`docs/reference/cli.md`, `docs/guide/debugging.md`). A typed on-disk format needs a pretty printer for this path or a flag that keeps the old JSON for one release.

`src/pyscriptc/debug.py`. `--debug-dir DIR` writes `ir.json` as pretty JSON of the same live dict, claimed identical to `<out>.ir.json`. `summary.json` is not IR (timings, status); leave it.

`src/pyscriptc/lower/` (17 files). Strangler round-trip, not disk. Files: `__init__.py`, `_annotations.py`, `_call_attr.py`, `_calls.py`, `_calls_more.py`, `_classes.py`, `_classes_more.py`, `_closures.py`, `_core.py`, `_coro_adapter.py`, `_expr.py`, `_expr_compare.py`, `_misc_emit.py`, `_stdlib.py`, `_stmt.py`, `_stmt_a.py`, `_stmt_b.py`. Heaviest `node_to_dict` files: `_calls.py` (41), `_core.py` (23), `_stmt_b.py` (20), `_call_attr.py` (16), `_expr.py` / `_expr_compare.py` (11 each). Heaviest `dict_to_node`: `_calls.py` (57), `_stmt_b.py` (32), `_call_attr.py` (26). Dropping the dict IR means these helpers return typed nodes and stop reconstructing dicts; that is lowering work, not a new file format, but every disk dump downstream still sees dicts until they stop.

`restlower/` has no `node_to_dict` / `dict_to_node`. Emit (`emit/llvm/`, `emitter.py`) consumes the dict in memory and does not dump it. `ir.py` / `irverify.py` validate the dict in memory. `package.py` merges module dicts in memory. Those are consumers (handoff 4), not serializers.

## CLI, debug, and scripts

`pyscriptc compile FILE --emit ir` is the named dump. The compiler also writes `.ir.json` on the llvm/obj/bin paths so later stages and `--report` can reread fences and the boxed plane map. `--debug-dir` copies that dump to `ir.json`. `.agents/docs/debug.md` documents both.

`scripts/gen_emitter_goldens.py` builds dict modules with local helpers (`expr`, `stmt`, `fn`, `make_module`) and writes 36 pretty JSON fixtures plus `tests/fixtures/backend/manifest.json`. A typed on-disk format either regenerates those fixtures from typed constructors or keeps a committed JSON adapter until the goldens move to MIR / LLVM-only.

No pickle, marshal, or msgpack of the IR exists in compiler code. User-program `pickle` is fenced (PS2002). `src/pyscriptc/runcache.py` caches binaries and a stamp JSON, not IR. `benchmarks/compat2/cache.py` caches scorer outcomes keyed on probe + oracle + compiler file hashes; it does not store IR. The "pure-dep IR cache" named in `.agents/plans/2026-10-21-pypi-support-design.md` is still a plan item, not a module. A typed on-disk format does not have to replace a cache that is not there.

## Scorer and gate paths

`benchmarks/compat2/irscore.py` lowers each visible probe to a module dict, `canonical.hash_module`, writes/compares `probes/IR_HASHES.json`. Frozen; A/B regen only after a recorded decision (R-1).

`benchmarks/compat2/tirscore.py` same lowering, `tirserde.hash_module`, `probes/TIR_HASHES.json`, plus `--verify-bytes` against `canonical.hash_module`. Nightly-gates workflow compares the TIR baseline. Frozen file.

`benchmarks/compat2/fndigest.py` loads source (via `lower_file`) or an `ir.json` dump, then `hash_module` per function, class, header, and fences. Debugging aid for hash diffs; deserializes pretty JSON as a module dict.

`benchmarks/compat2/fixpoint.py` hashes every importable `src/pyscriptc` module's canonical IR twice. Writes `fixpoint.json` (hash map, not IR).

`benchmarks/compat2/score.py` may invoke `--emit ir` for a fence/build row; it does not keep the dump as a cache.

`benchmarks/compat2/bundle.py` and `benchmarks/library/run.py` `json.loads` `<binary>.ir.json` only to read `["fences"]`.

`benchmarks/compat/score.py` (v1, frozen history) compiles `--emit ir` and `json.loads` the dump. Retirement of v1 is separate from dict-IR deletion; until then it pins pretty JSON.

`benchmarks/compat2/coverage_map.py` lowers and validates in memory; it does not serialize IR.

Holdout probes, if hashed, go through the same `irscore` / `tirscore` drivers when `holdout=True`. This note does not list holdout files.

## Tests and helpers

T-0 owns classifying `"kind"` assertions. This section only lists tests that serialize or deserialize the dict IR, so T-1 does not mistake them for behavior tests.

Pretty-dump / `--emit ir` consumers (CLI round-trip or existence checks): `test_band.py`, `test_capability.py`, `test_cli_dx.py`, `test_constructors.py`, `test_coverage.py`, `test_debug.py`, `test_deps_compile.py`, `test_deps_import_names.py`, `test_deps_receipt.py`, `test_fallback_definitions.py`, `test_first_run.py`, `test_irverify.py`, `test_lwave.py`, `test_package.py`, `test_program.py`, `test_r26_optional.py`, `test_r29_collections.py`, `test_reach_pruning.py`, `test_reexports.py`, `test_spike.py`. Several of these `json.loads` the dump to read `fences` or the module; `test_debug.py` asserts `--debug-dir/ir.json` equals `<out>.ir.json` byte for byte.

`json.dumps(module)` in-process (no disk): `test_coro_async.py`, `test_coro_asyncio_sync.py`, `test_coro_generators.py`, `test_coro_stage_c.py`, `test_coro_tasks.py`, `test_coro_timeouts.py`, `test_spike.py`. These search the dumped text for yield/await markers. R-2 should say whether those become typed-node walks.

Golden IR deserialize: `test_emitter_goldens.py`, `test_emitter_package.py`, `test_emitter_seam.py` load `tests/fixtures/backend/llvm/*.ir.json` (36 files) and emit LLVM. `test_emitter_seam.py` also rebuilds the corpus in a scratch dir and compares dicts.

Canonical hash tests: `test_canonical.py` (class (b) for T-0: pins dict-shape canonical rules on purpose), `test_e1_entry.py`, `test_e1_append.py` (pinned hex digests of lowered modules), `test_irnode_import.py` (`hash_module_node` vs `hash_module`).

Typed reconstruction tests: `test_irnode_serde.py` (`node_to_dict` equality to dict literals; class (b)), `test_tirserde_island_call.py` (round trip).

`dict_to_node` as a test helper to read lowered dicts as typed nodes: `test_ffi_agg.py`, `test_ffi_handles.py`, `test_ffi_lower.py`, `test_island_scope_handles.py`, `test_t2_kwargs.py`, `test_t2_static_folds.py`. T-1 may keep the helper until lowering returns nodes; that is not a disk format.

No shared `tests/conftest.py` IR dump helper. Goldens are the only committed IR JSON besides the hash oracles.

## Not dict-IR serialization

`debug.py` `summary.json`, CLI `.report.json` / fence reports, `runcache.py` stamps, `compat2/cache.py` outcome JSON, census pins, telemetry, `verify.py` call-args JSON, `typefacts.py` checker protocol, `imports.py` table dumps, user-program `json.dumps` lowering in `_calls_more.py`. MIR `serialize()` / `module_hash` in `mir.py` is a different text form (handoff MIR notes), not the dict IR.

Emitters (`emitter.py`, `emit/llvm/*`) read the in-memory dict and write LLVM text. Deleting the dict IR does not by itself change `.ll` goldens if a typed or MIR seam feeds the same emitter.

## What a typed on-disk format would require

Keep SHA-256 and the run-twice-`cmp` ritual. The serializer decision already chose streaming over field order of the typed node, payload never materialized as a dict, baseline `TIR_HASHES.json`. Until R-1 retires `IR_HASHES.json`, any on-disk change must stay byte-identical to today's canonical compact JSON (`tirscore --verify-bytes`).

Pretty dumps (`--emit ir`, `--debug-dir/ir.json`, emitter goldens) are a second contract: indent 2, key order as the live dict, spans kept. A typed pretty printer must either match that JSON (so `test_debug.py` and golden determinism stay) or version the dump (`ir.json` vs `tir.json`) and update CLI docs in the same PR. Silent drift between `<out>.ir.json` and `--debug-dir/ir.json` is a bug today; keep that invariant.

`node_to_dict` must stop being the hash path. `payload_bytes_node` / `hash_module_node` reconstructing a dict then calling `canonicalize` still pays the dict tax. The typed walker has to implement the same strip/sort/path-scrub rules without a dict copy, or those rules move into the node (no `span` on the hashed fields, classes already ordered, fences already ordered).

Emitter goldens are committed dict IR. Options: (1) generate goldens from typed constructors and dump via the typed pretty printer, byte-identical; (2) stop committing IR and generate in `test_emitter_goldens.py` from the same builders; (3) move the seam to MIR text. (1) is the smallest typed-format task. (2) and (3) are separate design choices.

CLI `--emit ir` is the public dump. User docs currently say "the lowered module". A typed format is a compatibility break unless the JSON bytes stay equal. Prefer keeping JSON as a debug view produced from typed nodes, not a second IR.

v1 `benchmarks/compat/score.py` and fence-only readers (`bundle.py`, `library/run.py`, several tests) only need `module["fences"]` (and boxed `fallback` on the report path). They do not need a full typed dump if the CLI still writes a small sidecar; that is an owner choice.

No pure-dep IR cache to migrate. Do not invent one in the typed format.

Holdout: same drivers as the visible corpus; do not special-case a format.

## Task breakdown (one-hour tasks)

1. After T-0 / R-1 / R-2 merge, confirm the three cross-links still resolve; drop the racing-plans duplicate if both R-3 notes would land.

2. Add a `--debug=stages` line (or a comment in `cli/_main.py`) that names the pretty-JSON write as its own cost center; the profiling plan already called out `.ir.json` as the second-largest unaccounted cost. Implementation, not this note.

3. Prototype typed pretty-print of one fixture (`assign.ir.json`) from `_irnode` via a walker that does not call `node_to_dict`, and `cmp` against the committed file. Stop if bytes differ; record the first mismatched key. Do not commit goldens or hashes.

4. Same prototype for canonical compact bytes: `payload_bytes_node` without `node_to_dict`, `cmp` to `canonical.hash_module` on `examples/sum_squares.py`. Stop on mismatch.

5. List CLI flags and docs lines that mention `ir.json` (`docs/reference/cli.md`, `docs/guide/debugging.md`, `.agents/docs/debug.md`, `--debug-dir` help text) so a format change can update them in one PR.

6. Decide (owner) whether fence-only readers may consume a sidecar instead of the full dump, shrinking `--emit ir` dependents before the typed pretty printer exists.

7. After R-1's order of removal is written, mark which rows in this inventory die with `canonical.py` versus which must survive as the typed dump.

## Owner decisions still open

Whether `--emit ir` remains public JSON after the dict IR is deleted, or becomes a debug-only typed dump under a new flag.

Whether emitter goldens stay committed JSON or become generated-from-builders.

Whether `payload_bytes_node` may keep a private `node_to_dict` until the typed walker matches bytes, or must drop it before dict deletion (strangler vs hard cut).

Whether v1 `benchmarks/compat/score.py` is allowed to keep pretty JSON after v2 gates move, given v1 is frozen history.

Whether a per-dependency IR cache, if it is ever built, is typed-only and never learns the dict shape.

R-1 still owns the 14-nightly clock and the deletion order of `IR_HASHES.json` / `canonical.py` / `ir.py`. This inventory does not grant retirement.

R-2 still owns whether `lower/_coro_adapter.py` can shrink to a typed shim before `coro.py` moves. This inventory only records that the adapter is a `node_to_dict` site.

T-0 / T-1 still own the 39 `"kind"` test files. This note lists serde tests so they are not migrated twice and so class (b) pins (`test_canonical.py`, `test_irnode_serde.py`, e1 digest pins, emitter goldens) are not weakened.

## Gaps

Line counts in `.agents/notes/2026-11-03-pipeline-seams.md` are from 2026-11-03 (`tirserde.py` was 153 lines on `work/tirgate`; the file is larger now). This inventory did not re-`wc` every consumer.

`test_emitter_package.py` and `test_emitter_seam.py` were easy to miss because they do not spell `ir.json`. Other `entry["ir"]` style loads were grepped; only those two extra files showed up.

Dynamic construction of dict IR in tests that never dump JSON (hand-built `{"kind": ...}` literals) is T-0, not counted here except where those literals are the expected value of `node_to_dict`.

If a future cache writes IR under `$PYSC_CACHE_DIR`, it is not in this tree today; re-run the `rg` set in the headline table before deletion.
