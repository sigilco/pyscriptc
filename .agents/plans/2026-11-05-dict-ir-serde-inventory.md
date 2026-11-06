# R-3: every module that still serializes or deserializes the dict IR

Date: 2026-11-05. Epic: #817. Docs only. Measured on master with `rg node_to_dict`, `rg dict_to_node`, `rg hash_module(`, `rg 'json.dumps(module'`.

"Serialize" here means producing JSON bytes or a canonical digest of a module IR, or reconstructing that module from JSON or from a typed node. User-program `json.dumps` lowering (`lower/_calls_more.py`) is not dict IR and is omitted.

There is no per-dependency IR cache in the tree. `.agents/plans/2026-10-21-pypi-support-design.md` names it as later work. `benchmarks/compat2/cache.py` caches scorer outcomes keyed by probe and compiler-file hashes, not IR payloads. `runcache.py` caches binary stamps. Do not treat those as dict-IR stores.

## In-process typed round trip

| Module | Direction | Format |
| --- | --- | --- |
| `src/pyscriptc/tirserde.py` | both | `node_to_dict` / `dict_to_node` / `dict_to_type` / `dict_to_span`. Typed node to the exact dict the lowerer builds, and back. Island/print/exit extra keys stay dict (`_CALL_KEYS`). `hash_module_node` is `hash_module(node_to_dict(module))`. |
| `src/pyscriptc/lower/_core.py` and the other `lower/_*.py` files that import tirserde | both | Build typed nodes, immediately `node_to_dict` so the rest of the compiler still sees dicts. Reverse `dict_to_node` at helper boundaries. This is the strangler, not an on-disk format. |
| `src/pyscriptc/lower/_coro_adapter.py` | typed to dict | `coro_module`: `node_to_dict` on `functions` and `classes` before `coro.py`. |

A typed on-disk format does not need these in-process conversions to remain, but they are the migration tax until the emitter and `coro.py` read `IrNode`.

## Canonical bytes and hashes (no file, a digest)

| Module | Direction | Format |
| --- | --- | --- |
| `src/pyscriptc/canonical.py` | dict to bytes | `canonicalize` then `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`. Schema `canonical-schema/2`. Path scrubbing. Sort `classes`, fence records, handler `codes`. |
| `src/pyscriptc/tirserde.py` (`payload_bytes`, `hash_module`) | dict to bytes | Streaming SHA-256 of the same payload, no extra dict materialization of the JSON object. Still canonicalizes a dict first. |
| `benchmarks/compat2/irscore.py` | dict to `IR_HASHES.json` | Per-probe `canonical.hash_module`. Baseline JSON: header plus `probes` map. Regen ritual 2026-10-22. Frozen. |
| `benchmarks/compat2/tirscore.py` | dict to `TIR_HASHES.json` | Per-probe `tirserde.hash_module`. Same ritual. |
| `benchmarks/compat2/fixpoint.py` | dict to digest map | Per compiler module, `hash_module` twice (self-vs-self). |
| `benchmarks/compat2/fndigest.py` | dict to per-unit digest | Hashes each function/class via `canonical.hash_module({"unit": value})`. Reads a lowered module or an `ir.json` dump. |

A typed on-disk format for the gate is already specified: ir-canonicity.md 7.1, node id then schema field order, never a dict. That replaces `canonical.py` + `json.dumps`, not `--emit ir` pretty JSON.

## On-disk JSON that is the dict IR

| Module | Direction | Format |
| --- | --- | --- |
| `src/pyscriptc/cli/_main.py` (`--emit ir`) | write | Pretty `json.dumps(module, indent=2)` to `.ir.json`. This is the raw module dict after validate, including spans and `sourceFile`. Not the canonical hash payload. |
| `src/pyscriptc/debug.py` (`--debug-dir`) | write | `ir.json` same pretty dump. `summary.json` is debug metadata, not IR. |
| `src/pyscriptc/cli/_main.py` (`_handle_run_report`, boxed) | read | `json.loads` of `{binary}.ir.json` to recover `fallback` / island plane. Deserialize of the pretty dump. |
| `tests/fixtures/backend/llvm/*.ir.json` | read (goldens) | Committed pretty dict modules. `tests/test_emitter_goldens.py` loads them and emits LLVM. |
| Tests that `--emit ir` then `json.loads` fences | read | Many files (`test_package.py`, `test_program.py`, `test_deps_compile.py`, ...). They use the pretty dump as a fence/entry oracle, not as a hash. |

`ir.py` `validate_module` and `irverify.py` `verify_module` consume the in-memory dict, usually without a disk round trip. They are schema/structure over the same shape the pretty dump writes.

## Not dict IR (listed so they are not migrated as serde)

- `runcache.py`: version/stamp/digest of a binary.
- `benchmarks/compat2/cache.py`: scorer result cache.
- `imports.py` JSON: import table data.
- `typefacts.py` / checker JSON: TSP and checker wire formats.
- `telemetry.py`, `log.py`: JSONL events.
- Runtime `ps_json_dumps` / user `json.dumps`.

## What a typed on-disk format would require

Two different payloads, do not collapse them.

1. Debug / `--emit ir` pretty form. Today: indent-2 dict JSON with spans. Typed form: either keep writing `node_to_dict` (strangler, no user-visible change) or a versioned HIR JSON (`tir_schema` + field order, spans optional). Tools that `json.loads` `.ir.json` for fences (`mod["fences"]`) need a stable module envelope even if statement bodies become typed records. One-hour task: list every `json.loads(.*ir.json)` test and which keys it reads.
2. Gate / canonical form. Specified: stream typed nodes in id then field order, SHA-256, `TIR_HASHES.json`. Must stay byte-identical to today's canonical JSON until the owner retires `IR_HASHES.json` (R-1). A format that is not byte-identical is a hash-file move and is blocked.
3. Emitter goldens. Today: dict `.ir.json` plus `.ll`. Typed goldens would be a second corpus or a loader that `dict_to_node`s the existing files. Recommended: keep the committed dict fixtures until the emitter reads `IrNode` from `dict_to_node` in the golden runner (in-process, no file format change), then consider a typed dump.
4. Boxed report fallback. Reads `mod.get("fallback")` from `.ir.json`. A typed dump must keep that key on the envelope or write `.report.json` always (the CLI already tries the report file first).
5. `fndigest` A/B. Needs a way to hash one function. Typed: hash one `IrNode` body plus the function record fields through the streaming serializer, without wrapping `{"unit": dict}`.
6. Per-dependency IR cache (unbuilt). If it is built, it must use the typed canonical bytes (TIR), not `IR_HASHES.json` and not pretty `--emit ir` JSON. Pretty JSON includes spans and absolute paths.

## One-hour tasks

1. Table of every `json.loads` of `.ir.json` in tests: file, keys read, whether fences-only.
2. Decide `--emit ir` v2: keep pretty dict via `node_to_dict`, or add `--emit tir` and leave `--emit ir` until R-1.
3. Golden runner: load fixture, `dict_to_node` the bodies, emit through the seam that already has dual accessors (`emit/_ir.py` still indexes dicts today; that is a prior emitter PR, not this note).
4. `fndigest` typed unit hash helper (no baseline edit).
5. Boxed report: stop reading `.ir.json` when `.report.json` exists (already the first branch); add a test so the IR dump is not a required deserialize for island.
6. Spec patch: ir-canonicity.md names pretty `--emit ir` as non-canonical (already implied; make it one paragraph so agents stop hashing debug dumps).
7. Do not implement a pure-dep IR cache here; if scheduled, open it on TIR bytes with path scrubbing.

## Owner decisions still open

- Keep pretty `--emit ir` as `node_to_dict` forever (debug convenience) versus a typed dump that tools must relearn.
- Emitter goldens: convert files, or convert only the loader.
- Whether `fndigest` is allowed to hash typed nodes before `IR_HASHES.json` retires (yes if digests stay equal).
- Per-dependency IR cache: TIR bytes or not at all in v0.9.0.
