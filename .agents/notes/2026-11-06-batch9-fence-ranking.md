# 2026-11-06 batch 9 fence ranking (handoff task F-0)

Ranking of the batch-9 modules by census pin and by fence kind, as input to the follow-on unfence PRs.
Data: `tests/census_pins.json` on master plus a measured per-module breakdown (`byCode`, `byFamily`, top-15 messages per module) captured with `uv run python -m pyscriptc coverage <path> --json`.

`coro.py` (pin 328) and `tirserde.py` (pin 53) are **skipped** for this batch: coro is a dedicated epic, and tirserde's fences are 43/53 `Any is not a lowered type in the IR` (dynamic-annotation), which wants a different plan than the mechanical rewrites below.
Neither is a work item here.

## Ranking by pin

| module | pin | dominant families (count) |
| --- | ---: | --- |
| `cli/_main.py` | 553 | name-resolution 278, construct-other 84, field-lookup 67, local-inference 39, unlowered-call 28 |
| `ffi_manifest.py` | 122 | keyword-args 37, name-resolution 31, construct-other 18, field-lookup 13 |
| `typefacts.py` | 75 | call-arity 19, generic-alias 12, name-resolution 11, unlowered-call 8, unlowered-type-name 7 |
| `check.py` | 66 | name-resolution 30, local-inference 15, keyword-args 5, construct-other 5 |
| `factsbridge.py` | 60 | field-lookup 17, name-resolution 12, unlowered-call 5, dynamic-annotation 5, union-types 5 |
| `stubs.py` | 49 | unlowered-type-name 14, name-resolution 8, construct-other 7, unlowered-call 5 |
| `package.py` | 47 | unlowered-type-name 13, name-resolution 8, construct-other 8, field-lookup 5 |
| `imports.py` | 44 | name-resolution 15, construct-other 8, decorators 4, export-cascade 3 |

`cli/_main.py` alone holds more fences than the rest of the batch combined, but its dominant bucket (278 × `X is not a local or parameter`) is one mechanical pattern: module-level `*_parser`/`log`/`sub` names read from inside functions.
It is not 278 distinct problems.

## Kind mapping

The coverage JSON carries no explicit "kind" label, so the kinds were mapped from `family` and `message` like this.

- **dict read**: `field-lookup` family (`X is not a field`, `X is not a method`) plus the message `the element type of this read is unresolved (Any container)` (it sits inside `local-inference`).
  Reads off dict/record-shaped values; the fix is typed accessors or a concretely typed container.
- **dict build**: message `dict entries have one key type and one value type`: heterogeneous or untyped dict literals at construction.
- **keyword-arg**: `keyword-args` family (`keyword arguments are not lowered`, `list.sort keyword key is not lowered`) plus `unexpected keyword argument <name>` messages, which count under `call-arity`.
- **tuple `in`**: `tuples need at least two fields`, emitted by the const-tuple membership fold when `x in (v,)` has a mistyped needle, plus `in is lowered for str, bytes, list, and dict` (not in any module's top 15, so small).
  `call to tuple is not lowered` is the `tuple()` constructor and counts as **other**.
- **other**: everything else, i.e. `name-resolution` misses (globals read as locals, `__file__`), unlowered type names (`Any`, `Mapping`, `ast.*`, `frozenset[str]`), unlowered calls, `union types are not in the IR`, closed-import-table misses, decorators, export cascades.

Only `byFamily` is complete data; `msgs` is each module's top 15, so message-level counts below can be slight undercounts.

## Per-module kind breakdown (in-scope modules only)

| module | pin | dict read | dict build | keyword-arg | tuple `in` | other |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `cli/_main.py` | 553 | ~67 + 30 (`Namespace.emit` 16, `Namespace.output` 14, `getattr` 12) | 0 | 4 | 0 | ~480 (name-res 278, construct 84) |
| `ffi_manifest.py` | 122 | ~19 (field-lookup 13, Any-container read 6) | 0 | 37 | 11 | ~55 (name-res 31, construct 18) |
| `typefacts.py` | 75 | 2 | 0 | 19 (`unexpected keyword argument`) | 2 | ~52 (generic-alias 12, name-res 11) |
| `check.py` | 66 | 0 | 0 | 5 | 0 | ~61 (name-res 30, local-inference 15) |
| `factsbridge.py` | 60 | ~17 (`TypeFacts.args is not a field` 16) | 4 | 1 | 0 | ~38 (name-res 12, unlowered-call 5) |
| `stubs.py` | 49 | 0 | 0 | 0 | 2 | ~47 (unlowered-type-name 14, name-res 8) |
| `package.py` | 47 | 5 | 0 | 0 | 0 | ~42 (unlowered-type-name 13, name-res 8) |
| `imports.py` | 44 | 0 | 0 | 3 (incl. `sort(key=)` 2) | 0 | ~41 (name-res 15, construct 8) |

## Cheapest wins for follow-on PRs

Ordered by fences removed per mechanical rewrite.

1. **`ffi_manifest` keyword-args (37)**: the single largest same-shape bucket outside `_main`.
   Manifest loader calls built with kwargs; normalize to positional calls.
   One pattern, one file.
2. **`typefacts` unexpected keyword args (19)**: `TypeFacts(args=...)`, `optional=...)`.
   Same rewrite shape as (1); ~12+7 hits on two kwargs.
3. **`check` + `_main` module-level names read as locals (~308 total: name-resolution in check 30, in _main 278)**: every `X is not a local or parameter` on `*_parser`, `log`, `sub`, `roots`, `kept`, etc. is the same missing-local/global pattern; the rewrite is naming/parameter plumbing, no behaviour change.
   Biggest absolute pin drop in the batch (up to ~278 off `_main`).
4. **`_main` Namespace attribute reads (~30 + `getattr` 12)**: `Namespace.emit`/`Namespace.output is not a field` and bare `getattr(ns, "x")` two-arg calls; swap to annotated reads, three-arg `getattr`, or direct attribute access on a typed record.
   Stacks on (3) in the same file.
5. **`factsbridge` `TypeFacts.args` reads (16) + dict literals (4)**: mostly one accessor pattern (`TypeFacts.args is not a field`); a typed accessor fixes most of it, and the dict-literal key/value split is a small second rewrite.
6. **`ffi_manifest` tuple `in` (11)**: `tuples need at least two fields` from `x in (v,)` single-element or mistyped membership folds; rewrite as `==`/`or` chains or `set[str]` membership.
   Small but self-contained.

Left to later, non-mechanical PRs: `unlowered-type-name` (`Any`, `Mapping`, `ast.*`), dominant in `stubs`, `package`, `tirserde`; `union types are not in the IR`, which needs an IR decision, not a rewrite; `__file__`/closed-import misses (`ast`, `sysconfig`, `zipfile`), which need import-table or stdlib-surface decisions; `_main` `construct-other` (84), a mix that wants its own breakdown before it is worth a PR.
