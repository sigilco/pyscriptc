# Pipeline seams

One page per stage of the compile pipeline as of master 2026-11-03 (worktree `work/seamdocs`, `src/pyscriptc` at 94 modules, 42,336 lines). For each stage: input contract, output contract, the gate that guards it, owning modules with line counts, the CLI flag that exposes its output, and whether the seam is typed or conventional. Line counts from `wc -l`; module boundaries inside `lower/` and `emit/llvm/` are package-internal and listed per file only where the seam actually sits there.

## 1. Parse (CPython `ast`)

Input: Python source text (`str(path.read_text())`). Output: `ast.Module` plus source text, handed straight to `lower_source`; never persisted. Gate: none; a syntax error is a CPython `SyntaxError`, PS-code diagnostics start below this line. Owners: stdlib `ast` (not owned). CLI: none, an internal seam. Typed? Convention only. `ast.Module` is the one type crossing it and the compiler treats `ast` as unlowered (375 PS1106 fences name `ast.*` annotations on master), so today's parse seam is deliberately outside the compiler's own type discipline.

## 2. Checker preflight (`--checker pyrefly|mypy|pyright`)

Input: the source file path plus a checker name. Output: a `CheckerBridge` (`factsbridge.py`, 773 lines) carrying type facts the lowering reads as hints; advisory only, absence must not change semantics. Gate: none; checker failures warn, never fence. Owners: `factsbridge.py`, `typefacts.py` (1,594), `sigregistry.py` (382). CLI: `--checker` / `--bridge` (`pyscriptc compile --bridge pyrefly`), timed as the `bridge` stage in `--debug=stages`. Typed? Convention only: the bridge is a `dict`-shaped facts bag (`Any`-typed in `cli.py`), the seam the 2026-11-02 reassessment counted 1,925 IR-dict annotation sites against.

## 3. Lower (AST to dict IR)

Input: `ast.Module` + source + bridge + dependency roots. Output: the module dict (`dict[str, Any]`), keys fixed by the inventory in `docs/reference` and `.agents/docs/spec/ir-canonicity.md` section 1; fences ride in `module["fences"]`. Gate: the closed import table and PS2002 wording fences at emission time; PS1106 (`unlowered-type-name`) and the rest of the fence families fire during lowering. Owners: `lower/__init__.py` (2,367, entry `lower_source`/`lower_file`), `lower/_calls.py` (2,471), `lower/_stmt.py` (2,400), `lower/_classes.py` (2,194), `lower/_expr.py` (1,850), `lower/_stdlib.py` (1,075), `lower/_core.py` (554), `lower/_closures.py`, `reduce.py` (558) as the reducer; `package.py` (1,179) for directory merges. CLI: reached via `--emit ir`. Typed? Convention only; this is the seam R3 (typed HIR strangler) replaces with `_irnode.py` dataclasses behind the admitted-module list.

## 4. JSON IR (`--emit ir`)

Input: the module dict. Output: canonical JSON bytes, `canonical.py` (167) strip/sort rules, validated by `ir.py` (473) and `irverify.py` (392). Gate: the frozen canonicity gate, `probes/IR_HASHES.json` plus `benchmarks/compat2/irscore.py` (regen ritual 2026-10-22, run-twice-`cmp`); the serializer draft adds the parallel `TIR_HASHES.json` gate over `tirserde.py` (153, on `work/tirgate`). Owners: `canonical.py`, `ir.py`, `irverify.py`, `tirserde.py`. CLI: `pyscriptc compile FILE --emit ir`, debug surface `--debug=stages` (`validate`, `verify` stages) and `--debug-dir ir.json`. Typed? Convention only today; the schema is prose, the only executable contract is the hash oracle. First seam to become typed once R3 lands the node dataclasses.

## 5. HIR (typed, when R3 lands)

Input: the dict IR, converted per the strangler slices. Output: `pyscriptc/_irnode.py` concrete dataclasses, the only cross-module importable IR types, admitted per the amended import-admission draft. Gate: `TIR_HASHES.json` streaming digests in node-id then schema-field order; digests move only through the repin ritual. Owners: `_irnode.py` (new), `tirserde.py`, migration slices touch one `lower/` module at a time. CLI: none initially; surfaces as new `--debug=stages` stage names (`hir_lower`, `hir_emit`) under the per-stage cost rule. Typed? Typed by construction (dataclasses), the first stage whose input and output are both enforced by a Python type rather than by a validator and a hash.

## 6. MIR (R4)

Input: typed HIR. Output: backend-shaped MIR, consumed only by the emit side, never re-read by lowering. Gate: the backend-only contract (draft 2026-11-02-mir-backend-only-contract.md) plus MIR digests in the `TIR_HASHES.json` header discipline; `mirverify.py` (planned) is the executable check. Owners: `mir.py`, `mirlower.py`, `miremit.py`, `mirverify.py` (all planned, R4), admitted to the import table by the amended admission list. CLI: a future `--emit mir` debug view, same stage-table treatment. Typed? Typed dataclasses on both ends; the contract that makes it independently developable is the backend-only rule (no lowering module may import MIR), enforced by review plus an import-linter style check, not yet by the interpreter.

## 7. Emit LLVM (`--emit llvm`)

Input: module dict (today) or MIR (after R4). Output: LLVM IR text `.ll`. Gate: fences block emission entirely (`llvm.py` refuses on any fence); `llvverify.py` (3.7K) plus `--debug=verify` runs the LLVM verifier. Owners: `emitter.py` (76, dispatch), `llvm.py` (30, entry), `emit/llvm/` package: `_driver.py` (19.7K), `_stmts.py` (22.2K), `_abi.py` (29.8K), `_calls.py` (20.7K), `_exprs.py` (16.1K), `_runtime.py` (16.3K), `_stdlib.py` (14.1K), `_funcs.py` (12.7K), `_print.py` (11.5K), `_core.py` (18.7K), `_ir.py` (5.6K); `emitlib.py` (311). CLI: `pyscriptc compile FILE --emit llvm` prints the `.ll` path; `--emit ir|llvm|asm|obj|bin|lib` selects downstream. Typed? Convention only: the `.ll` text is checked by the external verifier, not by a type.

## 8. clang (`--emit obj|asm|bin`)

Input: `.ll` text. Output: object/asm/binary artifacts. Gate: target refusal checks (`refuse_unsupported_target`, wasm threads, `--emit lib` mobile-only via `require_mobile_target`); `zig cc emitted-IR smoke` in CI guards the emitted IR compiles. Owners: `cli.py` (1,376) subprocess orchestration, `_driver.py` for invocation shapes. CLI: `--emit obj|asm|bin`, `--target`, `--sanitize` (bin only), timed as `archive`/`cc` sub-steps, commands listed by `--debug=cc`. Typed? Convention; the seam is an argv list and a file path.

## 9. Link

Input: objects from the cc stage plus runtime archives (`runtime/`, native packages). Output: final binary or library bundle. Gate: `--sanitize` runtime choice, mobile/desktop triple checks; `wheel install smoke` and the release rehearsal cover the linked artifact. Owners: `cli.py` (the `link` stage, `dbg.begin/link` at `cli.py:1141`), `mobile.py` (67), `runcache.py` (23). CLI: implied by `--emit bin|lib` and `pyscriptc run`; `--debug=stages` splits the link into object compiles and the final link. Typed? Convention.

## Summary

Two of nine seams are enforced by a typed interface today (neither: even the IR seams are dict + validator + hash until R3/R4 land). Everything else is convention held in place by the fence families, the hash oracles, and CI.

## Five changes that would make the stages independently developable

1. Land `_irnode.py` (R3) and make the JSON IR seam consume and produce typed nodes, so lowering and emit stop sharing a `dict[str, Any]` whose only contract is a hash oracle.
2. Land the MIR backend-only contract (R4) with an import-direction check (no `lower/` import of `mir*`), so emit-side work cannot reach back into lowering state.
3. Admit the closed IR-module import list with one-line reasons, so cross-module typed imports stop fencing PS1106/PS2002 for exactly the IR modules and still fence everywhere else.
4. Give every stage the same debug surface: `dbg.begin`/`dbg.end` names plus wall ms and peak MB in `--debug=stages` and `summary.json`, with the recorded streaming-vs-dict baseline attached, so per-stage regressions are visible without a profiler.
5. Move the parse seam inside the type discipline: replace raw `ast.*` annotations with the compiler-owned node aliases (or the admitted typed forms) so the 375 PS1106 `ast.*` fences shrink as the lowering modules migrate, instead of being permanently outside the fence-at-the-boundary promise.
